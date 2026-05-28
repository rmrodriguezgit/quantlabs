from fastapi import FastAPI, HTTPException
import time
import re
import yfinance as yf
import cudf

app = FastAPI(title="QuantLab GPU Market Worker")
TICKERS = {"BTC-USD","ETH-USD","AAPL","NVDA","SPY","TSLA","QQQ","USDMXN=X","GC=F","CL=F"}
PERIODS = {"1d","5d","7d","1mo","3mo","6mo","1y","2y","5y"}
INTERVALS = {"1m","5m","15m","30m","1h","1d","1wk"}

def validate(ticker, period, interval):
    if not re.fullmatch(r"[A-Z0-9.\-=^]{1,40}", ticker): raise HTTPException(400, "ticker inválido")
    if period not in PERIODS: raise HTTPException(400, "period no permitido")
    if interval not in INTERVALS: raise HTTPException(400, "interval no permitido")

def records(df):
    out = df.copy()
    for col in out.columns:
        if str(out[col].dtype).startswith("datetime"):
            out[col] = out[col].astype("str")
    out = out.astype(object).where(out.notnull(), None)
    return out.to_dict("records")

@app.get("/health")
def health():
    return {"status":"ok","service":"QuantLab GPU Market Worker","engine":"cuDF","gpu":"NVIDIA CUDA"}

@app.get("/market")
def market(ticker: str="BTC-USD", period: str="7d", interval: str="5m"):
    validate(ticker, period, interval)
    t0=time.perf_counter()
    df = yf.download(ticker, period=period, interval=interval, progress=False, auto_adjust=False)
    if df.empty:
        raise HTTPException(404, "sin datos")
    df = df.reset_index()
    if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    gdf = cudf.from_pandas(df)
    close, high, low, volume = gdf["Close"], gdf["High"], gdf["Low"], gdf["Volume"]
    gdf["SMA20"] = close.rolling(20).mean()
    gdf["SMA50"] = close.rolling(50).mean()
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    gdf["RSI14"] = 100 - (100 / (1 + (gain / loss)))
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    gdf["MACD"] = ema12 - ema26
    gdf["MACDSignal"] = gdf["MACD"].ewm(span=9, adjust=False).mean()
    gdf["MACDHist"] = gdf["MACD"] - gdf["MACDSignal"]
    mid = close.rolling(20).mean()
    std = close.rolling(20).std()
    gdf["BBMid"] = mid
    gdf["BBUpper"] = mid + (2 * std)
    gdf["BBLower"] = mid - (2 * std)
    typical = (high + low + close) / 3
    gdf["VWAP"] = (typical * volume).cumsum() / volume.cumsum()
    pdf = gdf.to_pandas()
    last = pdf.iloc[-1]
    change_pct = ((pdf["Close"].iloc[-1] / pdf["Close"].iloc[0]) - 1) * 100
    return {
        "ticker": ticker, "period": period, "interval": interval, "mode": "gpu",
        "rows": int(len(pdf)), "latency_ms": round((time.perf_counter()-t0)*1000,2),
        "metrics": {"last": float(pdf["Close"].iloc[-1]), "high": float(pdf["High"].max()), "low": float(pdf["Low"].min()), "volume": float(pdf["Volume"].sum()), "change_pct": float(change_pct), "rsi": None if last["RSI14"] != last["RSI14"] else float(last["RSI14"]), "macd": None if last["MACD"] != last["MACD"] else float(last["MACD"]), "vwap": None if last["VWAP"] != last["VWAP"] else float(last["VWAP"])},
        "data": records(pdf.tail(250))
    }

# ─── LSTM GPU Forecasting ────────────────────────────────────────────────────
import pandas as pd
import numpy as np
import torch
from torch import nn
from scipy.optimize import minimize

STYLE_CONFIG = {
    "scalping": {"interval":"5m","period":"5d","steps":3,"freq":"5min","label":"Scalping"},
    "day_trading": {"interval":"15m","period":"5d","steps":4,"freq":"15min","label":"Day Trading"},
    "swing_trading": {"interval":"1h","period":"3mo","steps":5,"freq":"1h","label":"Swing Trading"},
    "position_trading": {"interval":"1d","period":"2y","steps":5,"freq":"1D","label":"Position Trading"},
    "long_term": {"interval":"1d","period":"5y","steps":10,"freq":"1D","label":"Inversión a largo plazo"}
}

def add_gpu_indicators(gdf):
    close, high, low, volume = gdf["Close"], gdf["High"], gdf["Low"], gdf["Volume"]
    delta = close.diff(); gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean(); loss = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    gdf["RSI14"] = 100 - (100 / (1 + (gain / loss)))
    ema12 = close.ewm(span=12, adjust=False).mean(); ema26 = close.ewm(span=26, adjust=False).mean()
    gdf["MACD"] = ema12 - ema26
    tr = cudf.concat([(high-low), (high-close.shift()).abs(), (low-close.shift()).abs()], axis=1).max(axis=1)
    gdf["ATR14"] = tr.rolling(14).mean()
    typical = (high+low+close)/3
    gdf["VWAP"] = (typical*volume).cumsum()/volume.cumsum()
    return gdf.dropna()

class PriceLSTM(nn.Module):
    def __init__(self, n_features, hidden=32):
        super().__init__(); self.lstm=nn.LSTM(n_features, hidden, batch_first=True); self.head=nn.Linear(hidden,1)
    def forward(self,x):
        out,_=self.lstm(x); return self.head(out[:,-1,:])

@app.get("/forecast/lstm")
def forecast_lstm(ticker: str="BTC-USD", style: str="scalping"):
    if not re.fullmatch(r'[A-Z0-9.\-=^]{1,40}', ticker): raise HTTPException(400,'ticker inválido')
    if style not in STYLE_CONFIG: raise HTTPException(400,"estilo inválido")
    cfg=STYLE_CONFIG[style]; t0=time.perf_counter()
    pdf=yf.download(ticker, period=cfg["period"], interval=cfg["interval"], progress=False, auto_adjust=False)
    if pdf.empty: raise HTTPException(404,"sin datos")
    pdf=pdf.reset_index()
    if hasattr(pdf.columns,"nlevels") and pdf.columns.nlevels>1: pdf.columns=[c[0] if isinstance(c,tuple) else c for c in pdf.columns]
    gdf=add_gpu_indicators(cudf.from_pandas(pdf))
    cols=["Close","MACD","RSI14","ATR14","VWAP"]
    arr=gdf[cols].to_pandas().astype("float32").values
    if len(arr)<80: raise HTTPException(422,"datos insuficientes")
    mu=arr.mean(axis=0); sd=arr.std(axis=0)+1e-6; z=(arr-mu)/sd
    seq=32; X=[]; y=[]
    for i in range(seq,len(z)): X.append(z[i-seq:i]); y.append(z[i,0])
    device="cuda" if torch.cuda.is_available() else "cpu"
    Xt=torch.tensor(np.array(X),device=device); yt=torch.tensor(np.array(y)[:,None],device=device)
    model=PriceLSTM(len(cols)).to(device); opt=torch.optim.Adam(model.parameters(),lr=0.01); lossfn=nn.MSELoss()
    model.train()
    for _ in range(35):
        opt.zero_grad(); loss=lossfn(model(Xt),yt); loss.backward(); opt.step()
    model.eval(); window=z[-seq:].copy(); preds=[]
    with torch.no_grad():
        for _ in range(cfg["steps"]):
            nxt=float(model(torch.tensor(window[None,:,:],device=device)).item()); preds.append(nxt)
            newrow=window[-1].copy(); newrow[0]=nxt; window=np.vstack([window[1:],newrow])
    pred_close=np.array(preds)*sd[0]+mu[0]
    time_col="Datetime" if "Datetime" in pdf.columns else "Date"
    last_ts=pd.to_datetime(pdf[time_col].iloc[-1], utc=True).tz_convert(None)
    future_times=pd.date_range(last_ts, periods=cfg["steps"]+1, freq=cfg["freq"])[1:]
    hist=gdf[[time_col,"Close"]].tail(120).to_pandas(); hist[time_col]=pd.to_datetime(hist[time_col], utc=True).dt.tz_convert(None).astype(str)
    forecast=[{"ds":str(ts),"yhat":float(v)} for ts,v in zip(future_times,pred_close)]
    last=float(arr[-1,0]); final=float(pred_close[-1])
    return {"ticker":ticker,"style":style,"style_label":cfg["label"],"interval":cfg["interval"],"period":cfg["period"],"steps":cfg["steps"],"engine":"lstm_gpu","device":device,"latency_ms":round((time.perf_counter()-t0)*1000,2),"metrics":{"last":last,"forecast_final":final,"forecast_change_pct":((final/last)-1)*100},"history":[{"ds":r[time_col],"y":float(r["Close"])} for _,r in hist.iterrows()],"forecast":forecast}


# ─── Portfolio Markowitz GPU ────────────────────────────────────────────────
DEFAULT_TICKERS = ["HWM", "PLTR", "NVDA", "V", "AMZN", "WM"]
DEFAULT_BENCHMARK = "SPY"
STOCK_CATALOG = ['WMT', 'AAPL', 'PLTR', 'MSFT', 'NVDA', 'GOOGL', 'AMZN', 'META', 'TSM', 'BRK-B', 'V', 'JPM', 'XOM', 'LLY', 'MRK', 'UNH', 'PG', 'MA', 'CVX', 'KO', 'PEP', 'COST', 'TMO', 'ORCL', 'CSCO', 'NKE', 'VZ', 'ASML', 'TXN', 'ABT', 'TM', 'SAP', 'AMD', 'NFLX', 'NOW', 'ADBE', 'LVMUY', 'BABA', 'SHEL', 'TMUS', 'QCOM', 'PFE', 'SNY', 'AZN', 'TOT', 'GSK', 'RIO', 'BHP', 'MCD', 'HWM', 'WM', 'ADMA', 'ALMU', 'AVGO', 'ASTS', 'BE', 'DAVE', 'POWL']
CRYPTO_CATALOG = ['BTC-USD', 'ETH-USD', 'USDT-USD', 'XRP-USD', 'LTC-USD', 'ADA-USD', 'DOT-USD', 'BCH-USD', 'XLM-USD', 'LINK-USD']
FX_CATALOG = ['EURUSD=X', 'MXN=X', 'USDMXN=X', 'CADMXN=X', 'JPYMXN=X', 'EURMXN=X', 'GBPMXN=X', 'GBPUSD=X', 'USDJPY=X', 'EURJPY=X', 'AUDUSD=X', 'CADUSD=X']
PORTFOLIO_BENCHMARKS = {"SPY", "QQQ"}
PORTFOLIO_PERIODS = {"6mo", "1y", "2y", "5y"}
PORTFOLIO_MAP = {"BTC": "BTC-USD", "BRK.B": "BRK-B", "MNX=X": "MXN=X", "MCD​": "MCD"}
PORTFOLIO_CATALOG = set(STOCK_CATALOG + CRYPTO_CATALOG + FX_CATALOG)

def normalize_portfolio_tickers(raw):
    out=[]
    for item in [x.strip().upper() for x in raw.split(',') if x.strip()]:
        item=PORTFOLIO_MAP.get(item,item)
        if item not in out: out.append(item)
    return out

def metric_pack(series, bench=None):
    wealth=(1+series).cumprod(); peak=wealth.cummax(); ann_ret=float(series.mean()*252); ann_vol=float(series.std(ddof=1)*np.sqrt(252)); downside=series[series<0].std(ddof=1)
    cumulative=float(wealth.iloc[-1]-1); years=max(len(series)/252,1/252); var95=float(series.quantile(.05)); tail=series[series<=var95]
    data={"annual_return":ann_ret,"annual_volatility":ann_vol,"sharpe":None if ann_vol==0 else float(ann_ret/ann_vol),"sortino":None if downside==0 or pd.isna(downside) else float(np.sqrt(252)*series.mean()/downside),"max_drawdown":float(((wealth/peak)-1).min()),"var_95_daily":var95,"cvar_95_daily":None if tail.empty else float(tail.mean()),"cumulative_return":cumulative,"cagr":float((1+cumulative)**(1/years)-1)}
    if bench is not None:
        bvar=float(bench.var(ddof=1)); beta=None if bvar==0 else float(np.cov(series,bench)[0,1]/bvar); data.update({"beta":beta,"alpha":None if beta is None else float((series.mean()-beta*bench.mean())*252)})
    return data

def solve_long_only(mu,cov,target=None,objective='min_variance'):
    n=len(mu); x0=np.repeat(1/n,n); bounds=[(0.0,1.0)]*n; cons=[{'type':'eq','fun':lambda w: np.sum(w)-1}]
    if target is not None: cons.append({'type':'eq','fun':lambda w,target=target: float(w@mu-target)})
    fun=(lambda w: -float((w@mu)/np.sqrt(w@cov@w)) if (w@cov@w)>0 else 1e9) if objective=='max_sharpe' else (lambda w: float(w@cov@w))
    res=minimize(fun,x0,method='SLSQP',bounds=bounds,constraints=cons,options={'ftol':1e-12,'maxiter':1000})
    if not res.success: raise HTTPException(422,f'optimización no convergió: {res.message}')
    return np.clip(res.x,0,1)

def exact_frontier(mu,cov,points=60):
    wmin=solve_long_only(mu,cov); lo=float(wmin@mu); hi=float(np.max(mu)); pts=[]
    for target in np.linspace(lo,hi,points):
        try:
            w=solve_long_only(mu,cov,float(target)); ret=float(w@mu); vol=float(np.sqrt(w@cov@w)); pts.append({'risk':vol,'return':ret,'sharpe':None if vol==0 else float(ret/vol)})
        except HTTPException: pass
    return pts,wmin

@app.get('/portfolio/markowitz')
def portfolio_markowitz(tickers: str=','.join(DEFAULT_TICKERS), benchmark: str=DEFAULT_BENCHMARK, period: str='2y'):
    t0=time.perf_counter(); names=normalize_portfolio_tickers(tickers); benchmark=benchmark.upper()
    if not (2<=len(names)<=10): raise HTTPException(400,'selecciona entre 2 y 10 activos')
    if any(not re.fullmatch(r'[A-Z0-9.\-=^]{1,40}', t) for t in names): raise HTTPException(400,'ticker inválido')
    if benchmark not in PORTFOLIO_BENCHMARKS or period not in PORTFOLIO_PERIODS: raise HTTPException(400,'parámetros inválidos')
    symbols=list(dict.fromkeys(names+[benchmark])); raw=yf.download(symbols,period=period,interval='1d',progress=False,auto_adjust=True,group_by='column')
    if raw.empty: raise HTTPException(404,'sin datos')
    close=raw['Close'].copy() if isinstance(raw.columns,pd.MultiIndex) else raw[['Close']].rename(columns={'Close':symbols[0]})
    close=close.dropna(how='all').ffill().dropna(); missing=[s for s in symbols if s not in close.columns]
    if missing: raise HTTPException(422,f"sin datos para: {', '.join(missing)}")
    pdf=close[names+[benchmark]].dropna(); gdf=cudf.from_pandas(pdf); gret=gdf[names].pct_change().dropna(); gb=gdf[[benchmark]].pct_change().dropna(); returns=gret.to_pandas(); bench=gb.to_pandas()[benchmark].reindex(returns.index).dropna(); returns=returns.loc[bench.index]
    mu=returns.mean().to_numpy()*252; cov=returns.cov().to_numpy()*252; frontier,wmin=exact_frontier(mu,cov); wmax=solve_long_only(mu,cov,objective='max_sharpe')
    rng=np.random.default_rng(42); sw=rng.dirichlet(np.ones(len(names)),size=10000); sr=sw@mu; sv=np.sqrt(np.einsum('ij,jk,ik->i',sw,cov,sw)); ssh=np.divide(sr,sv,out=np.full_like(sr,np.nan),where=sv>0)
    rmin=returns.mul(wmin,axis=1).sum(axis=1); rmax=returns.mul(wmax,axis=1).sum(axis=1); cum=pd.DataFrame({'date':returns.index.astype(str),'max_sharpe':((1+rmax).cumprod()-1).values,'min_variance':((1+rmin).cumprod()-1).values,'benchmark':((1+bench).cumprod()-1).values}); corr=returns.corr().round(6)
    return {'mode':'gpu','engine':'cudf_cuda','tickers':names,'benchmark':benchmark,'period':period,'rows':int(len(returns)),'method':'slsqp_exact_long_only','solver':'SLSQP','frontier_points':int(len(frontier)),'context_portfolios':int(len(sw)),'latency_ms':round((time.perf_counter()-t0)*1000,2),'max_sharpe':{'weights':{t:float(v) for t,v in zip(names,wmax)},'metrics':metric_pack(rmax,bench)},'min_variance':{'weights':{t:float(v) for t,v in zip(names,wmin)},'metrics':metric_pack(rmin,bench)},'benchmark_metrics':metric_pack(bench),'frontier':frontier,'cloud':[{'risk':float(v),'return':float(r),'sharpe':float(sh)} for v,r,sh in zip(sv[::10],sr[::10],ssh[::10])],'cumulative':cum.to_dict('records'),'correlation':{r:{c:float(corr.loc[r,c]) for c in corr.columns} for r in corr.index}}
