import numpy as np, pandas as pd, yfinance as yf
from .base import BaseTool
class FinancialTool(BaseTool):
    name='financial'
    def run(self, action: str, tickers: list[str], period: str='1y'):
        data=yf.download(tickers, period=period, auto_adjust=True, progress=False)['Close']
        if isinstance(data, pd.Series): data=data.to_frame()
        returns=data.pct_change().dropna()
        if action=='sharpe': return (returns.mean()/returns.std()*np.sqrt(252)).to_dict()
        if action=='sortino':
            downside=returns.where(returns<0).std(); return (returns.mean()/downside*np.sqrt(252)).to_dict()
        if action=='var': return returns.quantile(0.05).to_dict()
        if action=='cvar': return {c: returns[c][returns[c] <= returns[c].quantile(.05)].mean() for c in returns}
        if action=='markowitz':
            cov=returns.cov()*252; mu=returns.mean()*252; w=np.linalg.pinv(cov.values)@mu.values; w=w/w.sum(); return dict(zip(returns.columns, w.tolist()))
        raise ValueError('unsupported finance action')
