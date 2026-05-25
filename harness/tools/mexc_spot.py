from __future__ import annotations

import hashlib
import hmac
import time
from urllib.parse import urlencode
import requests
from config import settings
from .base import BaseTool

MEXC_BASE_URL = "https://api.mexc.com"

class MexcSpotTool(BaseTool):
    name = 'mexc_spot'

    def _credentials(self):
        key = (settings.mexc_api_key or '').strip()
        secret = (settings.mexc_api_secret or '').strip()
        if not key or not secret:
            raise PermissionError('MEXC credentials are not configured in server environment')
        return key, secret

    def _public(self, path: str, params: dict | None = None):
        r = requests.get(f'{MEXC_BASE_URL}{path}', params=params or {}, timeout=20)
        r.raise_for_status()
        return r.json()

    def _signed(self, method: str, path: str, params: dict | None = None):
        key, secret = self._credentials()
        payload = dict(params or {})
        payload['timestamp'] = int(time.time() * 1000)
        payload.setdefault('recvWindow', 5000)
        query = urlencode(payload, doseq=True)
        signature = hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
        payload['signature'] = signature
        headers = {'X-MEXC-APIKEY': key}
        r = requests.request(method.upper(), f'{MEXC_BASE_URL}{path}', params=payload, headers=headers, timeout=30)
        r.raise_for_status()
        return r.json()

    def run(self, action: str | None = None, role: str | None = None, **kwargs):
        action = action or kwargs.pop('task', None)
        if role not in {'admin', 'trader'}:
            raise PermissionError('mexc_spot is only allowed for admin/trader roles')
        if action == 'credentials_status':
            key, _ = self._credentials()
            return {'configured': True, 'api_key_hint': f'***{key[-4:]}', 'secret_exposed': False}
        if action == 'account_status':
            data = self._signed('GET', '/api/v3/account')
            return {
                'can_trade': data.get('canTrade'),
                'account_type': data.get('accountType'),
                'balances_count': len(data.get('balances') or []),
                'secret_exposed': False,
            }
        if action == 'scan_spot_long_candidates':
            return self._scan(kwargs.get('tickers') or kwargs.get('symbols') or [], kwargs.get('interval','15m'), int(kwargs.get('limit',200)))
        if action == 'place_market_buy':
            return self._place_market_buy(kwargs, role)
        raise ValueError('unsupported mexc_spot action')

    def _scan(self, tickers, interval: str, limit: int):
        if isinstance(tickers, str):
            tickers = [x.strip() for x in tickers.split(',') if x.strip()]
        limit = max(60, min(limit, 500))
        results=[]
        for raw in tickers[:40]:
            symbol = self._symbol(raw)
            try:
                klines = self._public('/api/v3/klines', {'symbol': symbol, 'interval': interval, 'limit': limit})
                closes=[float(k[4]) for k in klines]
                highs=[float(k[2]) for k in klines]
                lows=[float(k[3]) for k in klines]
                volumes=[float(k[5]) for k in klines]
                quote_volumes=[float(k[7]) for k in klines if len(k) > 7]
                price=closes[-1]
                rsi=self._rsi(closes)
                macd, signal, hist = self._macd(closes)
                vwap=self._vwap(highs,lows,closes,volumes)
                ema20=self._ema(closes,20)[-1]
                ema50=self._ema(closes,50)[-1]
                bb_lower, bb_mid, bb_upper = self._bollinger(closes)
                atr_pct = self._atr_pct(highs, lows, closes)
                volume_ratio = self._volume_ratio(volumes)
                quote_volume = quote_volumes[-1] if quote_volumes else price * volumes[-1]
                buy_core_signal = rsi <= 30 and hist < 0 and price < vwap
                sell_core_signal = rsi >= 70 and hist > 0 and price > vwap
                lower_band_touch = price <= bb_lower * 1.02
                upper_band_touch = price >= bb_upper * 0.98
                volume_confirmed = volume_ratio >= 0.8
                trend_not_extreme = price >= ema50 * 0.85
                long_signal = buy_core_signal and volume_confirmed and trend_not_extreme
                close_signal = sell_core_signal and volume_confirmed
                setup_score = self._setup_score({
                    'rsi_oversold': rsi <= 30,
                    'macd_negative': hist < 0,
                    'price_below_vwap': price < vwap,
                    'lower_band_touch': lower_band_touch,
                    'volume_confirmed': volume_confirmed,
                    'trend_not_extreme': trend_not_extreme,
                })
                sell_score = self._setup_score({
                    'rsi_overbought': rsi >= 70,
                    'macd_positive': hist > 0,
                    'price_above_vwap': price > vwap,
                    'upper_band_touch': upper_band_touch,
                    'volume_confirmed': volume_confirmed,
                })
                signal_name = 'BUY' if long_signal else ('SELL' if close_signal else 'NONE')
                results.append({
                    'symbol': symbol,
                    'price': round(price, 10),
                    'rsi': round(rsi, 2),
                    'macd': round(macd, 8),
                    'macd_signal': round(signal, 8),
                    'macd_histogram': round(hist, 8),
                    'vwap': round(vwap, 10),
                    'ema20': round(ema20, 10),
                    'ema50': round(ema50, 10),
                    'bb_lower': round(bb_lower, 10),
                    'bb_mid': round(bb_mid, 10),
                    'bb_upper': round(bb_upper, 10),
                    'atr_pct': round(atr_pct, 4),
                    'volume_ratio': round(volume_ratio, 3),
                    'quote_volume': round(quote_volume, 2),
                    'price_below_vwap': price < vwap,
                    'price_above_vwap': price > vwap,
                    'rsi_oversold': rsi <= 30,
                    'rsi_overbought': rsi >= 70,
                    'macd_negative': hist < 0,
                    'macd_positive': hist > 0,
                    'lower_band_touch': lower_band_touch,
                    'upper_band_touch': upper_band_touch,
                    'trend_not_extreme': trend_not_extreme,
                    'setup_score': setup_score,
                    'sell_score': sell_score,
                    'signal': signal_name,
                    'long_entry_signal': long_signal,
                    'close_signal': close_signal,
                    'risk': self._risk(atr_pct, volume_ratio, quote_volume),
                    'rule': 'BUY si RSI<=30, MACD hist<0, precio<VWAP y filtros; SELL si RSI>=70, MACD hist>0, precio>VWAP y volumen confirma; NONE si no cumple.',
                })
            except Exception as exc:
                results.append({'symbol': symbol, 'error': str(exc)[:300]})
        candidates=[r for r in results if r.get('long_entry_signal')]
        exits=[r for r in results if r.get('close_signal')]
        return {
            'interval': interval,
            'results': results,
            'long_candidates': candidates,
            'sell_candidates': exits,
            'close_candidates': exits,
            'secret_exposed': False,
        }

    def _place_market_buy(self, kwargs: dict, role: str | None):
        symbol=self._symbol(kwargs.get('symbol',''))
        quote_amount=float(kwargs.get('quote_amount_usdt', 5))
        dry_run=bool(kwargs.get('dry_run', True))
        confirm_phrase=kwargs.get('confirm_phrase')
        if quote_amount <= 0 or quote_amount > 25:
            raise ValueError('quote_amount_usdt must be between 0 and 25 for this protected tool')
        order={'symbol': symbol, 'side': 'BUY', 'type': 'MARKET', 'quoteOrderQty': f'{quote_amount:.2f}'}
        if dry_run:
            return {'dry_run': True, 'prepared_order': order, 'secret_exposed': False}
        if not settings.mexc_live_trading_enabled:
            raise PermissionError('live trading disabled; set MEXC_LIVE_TRADING_ENABLED=true on server')
        if confirm_phrase != 'EXECUTE_MEXC_LIVE_ORDER':
            raise PermissionError('missing live order confirmation phrase')
        data=self._signed('POST','/api/v3/order', order)
        return {'dry_run': False, 'order_id': data.get('orderId'), 'symbol': data.get('symbol'), 'side': data.get('side'), 'status': data.get('status'), 'secret_exposed': False}

    def _symbol(self, value: str):
        symbol=str(value or '').upper().replace('-','').replace('/','').strip()
        if not symbol:
            raise ValueError('symbol required')
        if not symbol.endswith('USDT'):
            symbol += 'USDT'
        return symbol

    def _ema(self, values, period):
        alpha=2/(period+1)
        ema=values[0]
        out=[]
        for v in values:
            ema=(v*alpha)+(ema*(1-alpha))
            out.append(ema)
        return out

    def _rsi(self, closes, period=14):
        if len(closes) <= period:
            return 50.0
        gains=[]
        losses=[]
        for prev, cur in zip(closes[:-1], closes[1:]):
            delta=cur-prev
            gains.append(max(delta,0))
            losses.append(abs(min(delta,0)))
        avg_gain=sum(gains[-period:])/period
        avg_loss=sum(losses[-period:])/period
        if avg_loss == 0:
            return 100.0
        rs=avg_gain/avg_loss
        return 100-(100/(1+rs))

    def _macd(self, closes):
        ema12=self._ema(closes,12)
        ema26=self._ema(closes,26)
        macd_line=[a-b for a,b in zip(ema12,ema26)]
        signal=self._ema(macd_line,9)
        return macd_line[-1], signal[-1], macd_line[-1]-signal[-1]

    def _vwap(self, highs,lows,closes,volumes):
        pv=0.0
        vv=0.0
        for high, low, close, volume in zip(highs,lows,closes,volumes):
            typical=(high+low+close)/3
            pv += typical*volume
            vv += volume
        return pv/vv if vv else closes[-1]

    def _bollinger(self, closes, period=20, mult=2):
        values = closes[-period:] if len(closes) >= period else closes
        mid = sum(values) / len(values)
        variance = sum((x - mid) ** 2 for x in values) / len(values)
        std = variance ** 0.5
        return mid - (mult * std), mid, mid + (mult * std)

    def _atr_pct(self, highs, lows, closes, period=14):
        if len(closes) < 2:
            return 0.0
        trs = []
        for i in range(1, len(closes)):
            trs.append(max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            ))
        sample = trs[-period:] if len(trs) >= period else trs
        atr = sum(sample) / len(sample)
        return (atr / closes[-1]) * 100 if closes[-1] else 0.0

    def _volume_ratio(self, volumes, period=20):
        if not volumes:
            return 0.0
        sample = volumes[-period - 1:-1] if len(volumes) > period else volumes[:-1]
        if not sample:
            return 1.0
        avg = sum(sample) / len(sample)
        return volumes[-1] / avg if avg else 0.0

    def _setup_score(self, flags: dict[str, bool]):
        return sum(1 for ok in flags.values() if ok)

    def _risk(self, atr_pct: float, volume_ratio: float, quote_volume: float):
        if quote_volume < 1000 or atr_pct > 8 or volume_ratio < 0.5:
            return 'Alto'
        if atr_pct > 4 or volume_ratio < 0.8:
            return 'Moderado'
        return 'Controlado'
