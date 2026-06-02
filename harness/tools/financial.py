import numpy as np
import pandas as pd
import yfinance as yf

from .base import BaseTool
from .polymarket import PolymarketTool


class FinancialTool(BaseTool):
    name = 'financial'

    def run(self, action: str, tickers: list[str], period: str = '1y', interval: str = '1d', role: str | None = None, **kwargs):
        tickers = tickers or ['BTC-USD']
        if action == 'technical_scalping':
            return self._technical_scalping(tickers[:6], period=period or '1mo', interval=interval or '1d')

        data = yf.download(tickers, period=period, auto_adjust=True, progress=False)['Close']
        if isinstance(data, pd.Series):
            data = data.to_frame()
        returns = data.pct_change().dropna()
        if action == 'sharpe':
            return (returns.mean() / returns.std() * np.sqrt(252)).to_dict()
        if action == 'sortino':
            downside = returns.where(returns < 0).std()
            return (returns.mean() / downside * np.sqrt(252)).to_dict()
        if action == 'var':
            return returns.quantile(0.05).to_dict()
        if action == 'cvar':
            return {c: returns[c][returns[c] <= returns[c].quantile(.05)].mean() for c in returns}
        if action == 'markowitz':
            cov = returns.cov() * 252
            mu = returns.mean() * 252
            w = np.linalg.pinv(cov.values) @ mu.values
            w = w / w.sum()
            return dict(zip(returns.columns, w.tolist()))
        raise ValueError('unsupported finance action')

    def _technical_scalping(self, tickers: list[str], period: str, interval: str):
        source = 'Yahoo Finance/yfinance'
        raw = self._download_yfinance(tickers, period, interval)
        results = []
        for ticker in tickers:
            frame = self._ticker_frame(raw, ticker, len(tickers) > 1)
            if frame is None or frame.empty or len(frame) < 20:
                fallback = self._chainlink_btc_frame(interval) if ticker.upper() in {'BTC', 'BTC-USD', 'BTC/USD', 'BTCUSDT'} else None
                if fallback is not None and not fallback.empty and len(fallback) >= 20:
                    frame = fallback
                    row_source = 'Polymarket Chainlink BTC/USD candles'
                else:
                    results.append({
                        'ticker': ticker,
                        'status': 'no_data',
                        'reason': 'datos insuficientes o proveedor limitado',
                        'source': source,
                    })
                    continue
            else:
                row_source = source
            close = frame['Close'].dropna()
            high = frame['High'].reindex(close.index).ffill()
            low = frame['Low'].reindex(close.index).ffill()
            volume = frame.get('Volume', pd.Series(index=close.index, dtype=float)).reindex(close.index).fillna(0)
            metrics = self._technical_metrics(close, high, low, volume)
            results.append({'ticker': ticker, 'status': 'ok', 'source': row_source, **metrics})
        return {'action': 'technical_scalping', 'period': period, 'interval': interval, 'source': source, 'results': results}

    def _download_yfinance(self, tickers: list[str], period: str, interval: str):
        try:
            return yf.download(tickers, period=period, interval=interval, auto_adjust=True, progress=False)
        except Exception:
            return pd.DataFrame()

    def _chainlink_btc_frame(self, interval: str):
        chainlink_interval = interval if interval in {'1m', '5m', '15m'} else '1m'
        try:
            candles = PolymarketTool()._chainlink_candles(chainlink_interval, limit=60)
        except Exception:
            candles = []
        rows = []
        for candle in candles or []:
            close = self._float_or_none(candle.get('close'))
            high = self._float_or_none(candle.get('high')) or close
            low = self._float_or_none(candle.get('low')) or close
            open_ = self._float_or_none(candle.get('open')) or close
            if close is None:
                continue
            ts = candle.get('time') or candle.get('timestamp') or candle.get('timestamp_ms')
            if ts is not None and float(ts) > 10_000_000_000:
                ts = float(ts) / 1000
            rows.append({
                'timestamp': pd.to_datetime(ts, unit='s', utc=True, errors='coerce') if ts is not None else pd.NaT,
                'Open': open_,
                'High': high,
                'Low': low,
                'Close': close,
                'Volume': self._float_or_none(candle.get('volume')) or 1.0,
            })
        if not rows:
            return None
        frame = pd.DataFrame(rows).dropna(subset=['Close'])
        if frame.empty:
            return None
        if frame['timestamp'].notna().any():
            frame = frame.set_index('timestamp')
        return frame[['Open', 'High', 'Low', 'Close', 'Volume']].sort_index()

    def _ticker_frame(self, raw, ticker: str, multi: bool):
        try:
            if isinstance(raw.columns, pd.MultiIndex):
                if ticker in raw.columns.get_level_values(-1):
                    return raw.xs(ticker, axis=1, level=-1).dropna(how='all')
                if ticker in raw.columns.get_level_values(0):
                    return raw.xs(ticker, axis=1, level=0).dropna(how='all')
            return raw.dropna(how='all')
        except Exception:
            return None

    def _technical_metrics(self, close, high, low, volume):
        ema20 = close.ewm(span=20, adjust=False).mean()
        ema50 = close.ewm(span=50, adjust=False).mean()
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        exp12 = close.ewm(span=12, adjust=False).mean()
        exp26 = close.ewm(span=26, adjust=False).mean()
        macd = exp12 - exp26
        macd_signal = macd.ewm(span=9, adjust=False).mean()
        macd_hist = macd - macd_signal
        tr = pd.concat([(high - low), (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        typical = (high + low + close) / 3
        vwap = (typical * volume).cumsum() / volume.replace(0, np.nan).cumsum()
        support = close.tail(20).min()
        resistance = close.tail(20).max()
        price = float(close.iloc[-1])
        atr_value = float(atr.iloc[-1]) if pd.notna(atr.iloc[-1]) else 0.0
        trend = 'bullish' if price > float(ema20.iloc[-1]) > float(ema50.iloc[-1]) else ('bearish' if price < float(ema20.iloc[-1]) < float(ema50.iloc[-1]) else 'mixed')
        long_score = sum([
            price > float(ema20.iloc[-1]),
            float(rsi.iloc[-1]) < 65 if pd.notna(rsi.iloc[-1]) else False,
            float(macd_hist.iloc[-1]) > 0 if pd.notna(macd_hist.iloc[-1]) else False,
            price > float(vwap.iloc[-1]) if pd.notna(vwap.iloc[-1]) else False,
        ])
        short_score = sum([
            price < float(ema20.iloc[-1]),
            float(rsi.iloc[-1]) > 35 if pd.notna(rsi.iloc[-1]) else False,
            float(macd_hist.iloc[-1]) < 0 if pd.notna(macd_hist.iloc[-1]) else False,
            price < float(vwap.iloc[-1]) if pd.notna(vwap.iloc[-1]) else False,
        ])
        signal = 'LONG WATCH' if long_score >= 3 else ('SHORT WATCH' if short_score >= 3 else 'NO TRADE')
        stop = price - atr_value if signal == 'LONG WATCH' else (price + atr_value if signal == 'SHORT WATCH' else None)
        target = price + 1.5 * atr_value if signal == 'LONG WATCH' else (price - 1.5 * atr_value if signal == 'SHORT WATCH' else None)
        return {
            'last_price': round(price, 6),
            'trend': trend,
            'signal': signal,
            'rsi14': self._round(rsi.iloc[-1]),
            'macd_histogram': self._round(macd_hist.iloc[-1]),
            'ema20': self._round(ema20.iloc[-1]),
            'ema50': self._round(ema50.iloc[-1]),
            'vwap': self._round(vwap.iloc[-1]),
            'atr14': round(atr_value, 6),
            'support_20': self._round(support),
            'resistance_20': self._round(resistance),
            'long_score': int(long_score),
            'short_score': int(short_score),
            'suggested_stop': self._round(stop),
            'suggested_target': self._round(target),
            'risk_note': 'solo análisis; no ejecuta órdenes',
            'observations': int(len(close)),
        }

    def _round(self, value):
        try:
            if value is None or pd.isna(value):
                return None
            return round(float(value), 6)
        except Exception:
            return None

    def _float_or_none(self, value):
        try:
            if value is None or value == '':
                return None
            return float(value)
        except Exception:
            return None
