from __future__ import annotations
from datetime import UTC, datetime
from zoneinfo import ZoneInfo
import math
import json
import re
import requests
from config import settings
from .base import BaseTool

MEXC_BASE_URL = "https://api.mexc.com"
POLYMARKET_WEB_BASE_URL = "https://polymarket.com"
POLYMARKET_TIMEZONE = ZoneInfo("America/New_York")

class PolymarketTool(BaseTool):
    name = 'polymarket'

    def _base_urls(self):
        gamma = (settings.gamma_api or '').strip().rstrip('/')
        data = (settings.data_api or '').strip().rstrip('/')
        clob = (settings.clob_api or '').strip().rstrip('/')
        if not any([gamma, data, clob]):
            raise PermissionError('Polymarket endpoints are not configured in server environment')
        return gamma, data, clob

    def _get(self, base: str, path: str, params: dict | None = None):
        if not base:
            raise PermissionError('requested Polymarket endpoint is not configured')
        url = f'{base}/{path.lstrip("/")}'
        r = requests.get(url, params=params or {}, timeout=25)
        r.raise_for_status()
        return r.json()

    def run(self, action: str | None = None, role: str | None = None, **kwargs):
        action = action or kwargs.pop('task', None)
        if role not in {'admin', 'trader', 'teacher'}:
            raise PermissionError('polymarket is only allowed for authenticated analyst roles')
        gamma, data, clob = self._base_urls()
        if action == 'endpoints_status':
            return {
                'gamma_configured': bool(gamma),
                'data_configured': bool(data),
                'clob_configured': bool(clob),
                'urls_exposed': False,
                'secrets_exposed': False,
            }
        if action == 'search_markets':
            query = kwargs.get('query') or kwargs.get('q') or kwargs.get('symbol') or kwargs.get('keyword') or ''
            limit = max(1, min(int(kwargs.get('limit', 10)), 50))
            params = {'limit': limit, 'active': str(kwargs.get('active', True)).lower(), 'closed': str(kwargs.get('closed', False)).lower()}
            if query:
                params['search'] = query
            raw = self._get(gamma, '/markets', params)
            markets = raw if isinstance(raw, list) else raw.get('markets') or raw.get('data') or []
            if query:
                needle = str(query).lower()
                ranked = [
                    m for m in markets
                    if needle in str(m.get('question') or m.get('title') or m.get('slug') or '').lower()
                ]
                if ranked:
                    markets = ranked + [m for m in markets if m not in ranked]
            return {'markets': [self._market_summary(m) for m in markets[:limit]], 'urls_exposed': False, 'secrets_exposed': False}
        if action == 'crypto_updown_markets':
            return self._crypto_updown_markets(gamma, clob, **kwargs)
        if action == 'btc_updown_scalping_signal':
            return self._btc_updown_scalping_signal(gamma, clob, **kwargs)
        if action == 'btc_updown_5m15m_coordinated_signal':
            return self._btc_updown_5m15m_coordinated_signal(gamma, clob, **kwargs)
        if action == 'btc_updown_deep_train':
            return self._btc_updown_deep_train(**kwargs)
        if action == 'btc_updown_deep_predict':
            return self._btc_updown_deep_predict(**kwargs)
        if action == 'market_detail':
            market_id = kwargs.get('market_id') or kwargs.get('id')
            if not market_id:
                raise ValueError('market_id required')
            raw = self._get(gamma, f'/markets/{market_id}')
            return {'market': self._market_summary(raw, detail=True), 'urls_exposed': False, 'secrets_exposed': False}
        if action == 'order_book':
            token_id = kwargs.get('token_id') or kwargs.get('asset_id')
            if not token_id:
                raise ValueError('token_id required')
            raw = self._get(clob, '/book', {'token_id': token_id})
            return {'book': self._book_summary(raw), 'urls_exposed': False, 'secrets_exposed': False}
        if action == 'recent_trades':
            token_id = kwargs.get('token_id') or kwargs.get('asset_id')
            params = {'limit': max(1, min(int(kwargs.get('limit', 20)), 100))}
            if token_id:
                params['asset_id'] = token_id
            raw = self._get(data, '/trades', params)
            trades = raw if isinstance(raw, list) else raw.get('trades') or raw.get('data') or []
            return {'trades': trades[:params['limit']], 'urls_exposed': False, 'secrets_exposed': False}
        raise ValueError('unsupported polymarket action')

    def _crypto_updown_markets(self, gamma: str, clob: str, **kwargs):
        asset = str(kwargs.get('asset') or kwargs.get('symbol') or 'btc').lower()
        if asset not in {'btc', 'bitcoin'}:
            raise ValueError('crypto_updown_markets currently supports bitcoin/btc')
        intervals = kwargs.get('intervals') or kwargs.get('interval') or ['15m', '5m']
        if isinstance(intervals, str):
            intervals = [intervals]
        include_books = bool(kwargs.get('include_order_books', True))
        now_ts = int(datetime.now(UTC).timestamp())
        markets = []
        for interval in intervals:
            seconds = self._interval_seconds(interval)
            epoch = (now_ts // seconds) * seconds
            event = self._get(gamma, f'/events/slug/btc-updown-{seconds // 60}m-{epoch}')
            market = (event.get('markets') or [{}])[0]
            summary = self._market_summary(market)
            summary.update({
                'event_slug': event.get('slug'),
                'event_title': event.get('title'),
                'interval': f'{seconds // 60}m',
                'epoch': epoch,
                'tokens': self._outcome_tokens(summary),
            })
            summary.update(self._timing_frame(event, seconds, now_ts))
            price_to_beat, price_to_beat_source = self._price_to_beat_from_market(market, event)
            summary.update(self._price_frame(epoch, now_ts, seconds, price_to_beat, price_to_beat_source))
            if include_books:
                for token in summary['tokens']:
                    raw = self._get(clob, '/book', {'token_id': token['token_id']})
                    token['book'] = self._book_summary(raw)
            markets.append(summary)
        return {'markets': markets, 'urls_exposed': False, 'secrets_exposed': False}

    def _btc_updown_scalping_signal(self, gamma: str, clob: str, **kwargs):
        threshold = float(kwargs.get('threshold', 0.8))
        intervals = kwargs.get('intervals') or ['15m', '5m']
        candle_interval = str(kwargs.get('candle_interval') or kwargs.get('scalping_interval') or '1m')
        lookback = int(kwargs.get('lookback', 240))
        if str(kwargs.get('lookback_window') or '').lower() in {'1d', '1day', '24h'} and candle_interval == '5m':
            lookback = 288
        markets_payload = self._crypto_updown_markets(
            gamma,
            clob,
            asset='btc',
            intervals=intervals,
            include_order_books=True,
        )
        prediction_interval = str(kwargs.get('prediction_candle_interval') or '1m')
        prediction_lookback = int(kwargs.get('prediction_lookback') or 90)
        klines = self._chainlink_klines(prediction_interval, prediction_lookback) or self._mexc_klines('BTCUSDT', prediction_interval, prediction_lookback)
        signals = []
        for market in markets_payload['markets']:
            target = market.get('price_to_beat_reference') or market.get('start_price_reference')
            end_time = market.get('end_time')
            prophet = self._prophet_probability(klines, target, end_time)
            orderbook = self._orderbook_probability(market)
            up_probability = prophet.get('up_probability')
            down_probability = 1 - up_probability if up_probability is not None else None
            preferred = None
            confidence = None
            if up_probability is not None:
                preferred = 'Up' if up_probability >= down_probability else 'Down'
                confidence = max(up_probability, down_probability)
            signals.append({
                'interval': market.get('interval'),
                'question': market.get('question'),
                'countdown': market.get('countdown'),
                'timezone': market.get('timezone'),
                'start_time_et': market.get('start_time_et'),
                'end_time_et': market.get('end_time_et'),
                'next_reset_time_et': market.get('next_reset_time_et'),
                'price_to_beat_reference': target,
                'price_to_beat_source': market.get('price_to_beat_source'),
                'start_price_reference': market.get('start_price_reference'),
                'current_price_reference': market.get('current_price_reference'),
                'price_delta_reference': market.get('price_delta_reference'),
                'side_now_reference': market.get('side_now_reference'),
                'prophet': prophet,
                'forecast_price_at_close': prophet.get('forecast_price_at_close'),
                'prediction_candle_interval': prediction_interval,
                'prediction_lookback': prediction_lookback,
                'prediction_lookback_label': kwargs.get('lookback_window') or f'{lookback} candles',
                'lstm': {
                    'status': 'not_configured',
                    'reason': 'LSTM requires an isolated TensorFlow/PyTorch runtime before enabling live inference.',
                },
                'orderbook_probability': orderbook,
                'preferred_side': preferred,
                'confidence': confidence,
                'meets_threshold': confidence is not None and confidence >= threshold,
                'threshold': threshold,
            })
        return {'signals': signals, 'markets': markets_payload['markets'], 'urls_exposed': False, 'secrets_exposed': False}

    def _btc_updown_5m15m_coordinated_signal(self, gamma: str, clob: str, **kwargs):
        threshold = float(kwargs.get('threshold', 0.8))
        min_edge = float(kwargs.get('min_edge', 0.03))
        max_spread = float(kwargs.get('max_spread', 0.08))
        min_ask_size = float(kwargs.get('min_ask_size', 1))
        min_seconds_to_close = int(kwargs.get('min_seconds_to_close', 45))
        payload = self._btc_updown_scalping_signal(
            gamma,
            clob,
            threshold=threshold,
            intervals=['5m', '15m'],
            candle_interval=kwargs.get('candle_interval') or '5m',
            lookback_window=kwargs.get('lookback_window') or '1d',
            lookback=int(kwargs.get('lookback') or 288),
            prediction_candle_interval=kwargs.get('prediction_candle_interval') or '1m',
            prediction_lookback=int(kwargs.get('prediction_lookback') or 90),
        )
        markets = {m.get('interval'): m for m in payload.get('markets') or []}
        signals = {s.get('interval'): s for s in payload.get('signals') or []}
        candidates = []
        for interval in ['5m', '15m']:
            signal = signals.get(interval) or {}
            market = markets.get(interval) or {}
            preferred = signal.get('preferred_side')
            micro = self._side_microstructure(market, preferred)
            probability = self._side_probability(signal, preferred)
            edge = probability - micro['ask'] if probability is not None and micro.get('ask') is not None else None
            reasons = []
            if not signal.get('meets_threshold'):
                reasons.append('confidence_below_threshold')
            if preferred not in {'Up', 'Down'}:
                reasons.append('missing_side')
            if (market.get('seconds_to_close') or 0) < min_seconds_to_close:
                reasons.append('too_close_to_close')
            if micro.get('ask') is None:
                reasons.append('missing_ask')
            if micro.get('spread') is None or micro['spread'] > max_spread:
                reasons.append('spread_too_wide')
            if micro.get('ask_size') is None or micro['ask_size'] < min_ask_size:
                reasons.append('insufficient_ask_depth')
            if edge is None or edge < min_edge:
                reasons.append('edge_too_small')
            candidates.append({
                'interval': interval,
                'preferred_side': preferred,
                'confidence': signal.get('confidence'),
                'probability': probability,
                'edge': round(edge, 4) if edge is not None else None,
                'microstructure': micro,
                'seconds_to_close': market.get('seconds_to_close'),
                'countdown': signal.get('countdown'),
                'price_to_beat_reference': signal.get('price_to_beat_reference'),
                'current_price_reference': signal.get('current_price_reference'),
                'forecast_price_at_close': signal.get('forecast_price_at_close'),
                'window_et': f"{signal.get('start_time_et')} - {signal.get('end_time_et')}",
                'passes_filters': not reasons,
                'reasons': reasons,
            })
        trade_side = None
        coordinator_reasons = []
        passing = [c for c in candidates if c['passes_filters']]
        sides = {c.get('preferred_side') for c in candidates if c.get('preferred_side') in {'Up', 'Down'}}
        if len(passing) < 2:
            coordinator_reasons.append('both_windows_must_pass_filters')
        if len(sides) > 1:
            coordinator_reasons.append('5m_15m_direction_conflict')
        if len(passing) == 2 and len(sides) == 1:
            trade_side = passing[0]['preferred_side']
        action = 'TRADE' if trade_side else 'NO_TRADE'
        return {
            'action': action,
            'side': 'UP' if trade_side == 'Up' else ('DOWN' if trade_side == 'Down' else 'NONE'),
            'strategy': 'BTC Up/Down coordinated 5m/15m paper signal',
            'threshold': threshold,
            'filters': {
                'min_edge': min_edge,
                'max_spread': max_spread,
                'min_ask_size': min_ask_size,
                'min_seconds_to_close': min_seconds_to_close,
            },
            'candidates': candidates,
            'reasons': coordinator_reasons,
            'signals': payload.get('signals') or [],
            'markets': payload.get('markets') or [],
            'urls_exposed': False,
            'secrets_exposed': False,
        }

    def _timing_frame(self, event: dict, interval_seconds: int, now_ts: int):
        start_dt = self._parse_dt(event.get('startTime'))
        end_dt = self._parse_dt(event.get('endDate'))
        start_ts = int(start_dt.timestamp()) if start_dt else (now_ts // interval_seconds) * interval_seconds
        end_ts = int(end_dt.timestamp()) if end_dt else start_ts + interval_seconds
        seconds_to_close = max(0, end_ts - now_ts)
        seconds_elapsed = max(0, now_ts - start_ts)
        start_utc = datetime.fromtimestamp(start_ts, UTC)
        end_utc = datetime.fromtimestamp(end_ts, UTC)
        now_utc = datetime.fromtimestamp(now_ts, UTC)
        start_et = start_utc.astimezone(POLYMARKET_TIMEZONE)
        end_et = end_utc.astimezone(POLYMARKET_TIMEZONE)
        now_et = now_utc.astimezone(POLYMARKET_TIMEZONE)
        return {
            'timezone': 'America/New_York',
            'timezone_label': start_et.tzname(),
            'start_time': start_utc.isoformat().replace('+00:00', 'Z'),
            'end_time': end_utc.isoformat().replace('+00:00', 'Z'),
            'now_et': self._format_et(now_et),
            'start_time_et': self._format_et(start_et),
            'end_time_et': self._format_et(end_et),
            'next_reset_time_et': self._format_et(end_et),
            'seconds_to_close': seconds_to_close,
            'seconds_elapsed': seconds_elapsed,
            'progress_pct': round(min(100, max(0, seconds_elapsed / interval_seconds * 100)), 2),
            'countdown': self._format_countdown(seconds_to_close),
            'reset_schedule_minutes': [0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55],
            'reset_schedule_timezone': 'America/New_York',
        }

    def _price_frame(self, epoch: int, now_ts: int, interval_seconds: int | None = None, price_to_beat: float | None = None, price_to_beat_source: str | None = None):
        chainlink = self._chainlink_price_frame(epoch, now_ts, interval_seconds) if interval_seconds else {}
        start_price = chainlink.get('start_price_reference') or self._btc_reference_price(epoch)
        current_price = chainlink.get('current_price_reference') or self._btc_current_price()
        target = price_to_beat if price_to_beat is not None else (chainlink.get('price_to_beat_reference') or start_price)
        target_source = price_to_beat_source or chainlink.get('price_to_beat_source') or ('mexc_window_open_fallback' if target is not None else None)
        delta = current_price - target if current_price is not None and target is not None else None
        return {
            'price_source_note': 'Polymarket resolves with Chainlink BTC/USD; price_to_beat and current price use Polymarket Chainlink candles when available, otherwise MEXC BTCUSDT fallback.',
            'price_to_beat_reference': target,
            'price_to_beat_source': target_source,
            'start_price_reference': start_price,
            'current_price_reference': current_price,
            'current_price_source': chainlink.get('current_price_source') or ('mexc_ticker_fallback' if current_price is not None else None),
            'price_delta_reference': round(delta, 2) if delta is not None else None,
            'side_now_reference': 'Up' if delta is not None and delta >= 0 else ('Down' if delta is not None else None),
            'price_snapshot_ts': datetime.fromtimestamp(now_ts, UTC).isoformat().replace('+00:00', 'Z'),
        }

    def _chainlink_price_frame(self, epoch: int, now_ts: int, interval_seconds: int | None):
        if not interval_seconds:
            return {}
        interval = f'{interval_seconds // 60}m'
        market_candles = self._chainlink_candles(interval, limit=60, end_time_ms=(epoch + interval_seconds) * 1000)
        market_candle = next((row for row in market_candles if int(row.get('time') or -1) == int(epoch)), None)
        live_candles = self._chainlink_candles('1m', limit=15)
        live_candle = live_candles[-1] if live_candles else None
        frame = {}
        if market_candle:
            open_price = self._float_or_none(market_candle.get('open'))
            if open_price is not None:
                frame.update({
                    'price_to_beat_reference': open_price,
                    'price_to_beat_source': f'polymarket_chainlink_{interval}_open',
                    'start_price_reference': open_price,
                })
        if live_candle:
            current = self._float_or_none(live_candle.get('close'))
            if current is not None:
                frame.update({
                    'current_price_reference': current,
                    'current_price_source': 'polymarket_chainlink_1m_close',
                })
        return frame

    def _chainlink_klines(self, interval: str, limit: int):
        rows = []
        end_time_ms = None
        remaining = max(1, min(int(limit), 1000))
        while remaining > 0:
            batch_limit = 60 if remaining >= 60 else (30 if remaining >= 30 else 15)
            candles = self._chainlink_candles(interval, limit=batch_limit, end_time_ms=end_time_ms)
            if not candles:
                break
            rows = candles + rows
            remaining -= len(candles)
            first_time = self._float_or_none(candles[0].get('time'))
            if first_time is None or len(candles) < batch_limit:
                break
            end_time_ms = int(first_time * 1000) - 1
        deduped = {int(row['time']): row for row in rows if row.get('time') is not None}
        out = []
        for ts in sorted(deduped)[-limit:]:
            close = self._float_or_none(deduped[ts].get('close'))
            if close is not None:
                out.append({'timestamp_ms': ts * 1000, 'close': close})
        return out

    def _chainlink_candles(self, interval: str, limit: int = 60, end_time_ms: int | None = None):
        params = {'symbol': 'BTC', 'interval': interval, 'limit': max(15, min(int(limit), 60))}
        if params['limit'] not in {15, 30, 60}:
            params['limit'] = 60 if params['limit'] > 30 else (30 if params['limit'] > 15 else 15)
        if end_time_ms is not None:
            params['endTime'] = str(int(end_time_ms))
        try:
            raw = self._polymarket_web_public('/api/chainlink-candles', params)
        except Exception:
            return []
        candles = raw.get('candles') if isinstance(raw, dict) else []
        return candles if isinstance(candles, list) else []

    def _price_to_beat_from_market(self, market: dict, event: dict) -> tuple[float | None, str | None]:
        candidates = (
            'priceToBeat', 'price_to_beat', 'strikePrice', 'strike_price',
            'targetPrice', 'target_price', 'initialPrice', 'initial_price',
            'openPrice', 'open_price', 'startPrice', 'start_price',
            'referencePrice', 'reference_price',
        )
        for source_name, payload in (('market', market), ('event', event)):
            for key in candidates:
                value = self._float_or_none(payload.get(key) if isinstance(payload, dict) else None)
                if value is not None and value > 0:
                    return value, f'polymarket_{source_name}.{key}'
        text_parts = []
        for payload in (market, event):
            if not isinstance(payload, dict):
                continue
            for key in ('question', 'title', 'description'):
                if payload.get(key):
                    text_parts.append(str(payload.get(key)))
        parsed = self._price_to_beat_from_text('\n'.join(text_parts))
        if parsed is not None:
            return parsed, 'polymarket_text'
        return None, None

    def _price_to_beat_from_text(self, text: str) -> float | None:
        if not text:
            return None
        patterns = (
            r'(?:price to beat|precio a superar|strike price|target price|starting price|start price|initial price)[^0-9$]{0,80}\$?([0-9][0-9,]*(?:\.[0-9]+)?)',
            r'\$([0-9][0-9,]{3,}(?:\.[0-9]+)?)',
        )
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                value = self._float_or_none(match.group(1).replace(',', ''))
                if value is not None and value > 0:
                    return value
        return None

    def _btc_current_price(self):
        data = self._mexc_public('/api/v3/ticker/price', {'symbol': 'BTCUSDT'})
        try:
            return float(data.get('price'))
        except (AttributeError, TypeError, ValueError):
            return None

    def _btc_reference_price(self, epoch: int):
        data = self._mexc_public('/api/v3/klines', {
            'symbol': 'BTCUSDT',
            'interval': '1m',
            'startTime': epoch * 1000,
            'endTime': (epoch + 60) * 1000,
            'limit': 1,
        })
        try:
            return float(data[0][1])
        except (IndexError, TypeError, ValueError):
            return None

    def _mexc_klines(self, symbol: str, interval: str, limit: int):
        raw = self._mexc_public('/api/v3/klines', {'symbol': symbol, 'interval': interval, 'limit': max(60, min(limit, 1000))})
        rows = []
        for row in raw:
            rows.append({
                'timestamp_ms': int(row[0]),
                'close': float(row[4]),
            })
        return rows

    def _mexc_public(self, path: str, params: dict | None = None):
        r = requests.get(f'{MEXC_BASE_URL}{path}', params=params or {}, timeout=20)
        r.raise_for_status()
        return r.json()

    def _polymarket_web_public(self, path: str, params: dict | None = None):
        r = requests.get(
            f'{POLYMARKET_WEB_BASE_URL}{path}',
            params=params or {},
            headers={'accept': 'application/json', 'user-agent': 'Quantlab/1.0'},
            timeout=20,
        )
        r.raise_for_status()
        return r.json()

    def _prophet_probability(self, klines: list[dict], target: float | None, end_time: str | None):
        """Short-window BTC nowcast for 5m/15m Polymarket events.

        The old day-scale trend/Prophet forecast was too far from actual closes for
        micro windows. This method anchors on the latest Chainlink close, adds a
        small bounded momentum term, and sizes probability with recent 1m noise.
        """
        if target is None or not end_time or len(klines) < 8:
            return {'status': 'insufficient_data', 'model': 'chainlink_1m_nowcast', 'up_probability': None}
        try:
            import numpy as np
        except Exception as exc:
            return {'status': 'unavailable', 'model': 'chainlink_1m_nowcast', 'up_probability': None, 'error': str(exc)[:160]}
        closes = np.array([self._float_or_none(row.get('close')) for row in klines], dtype=float)
        times = np.array([self._float_or_none(row.get('timestamp_ms')) for row in klines], dtype=float) / 1000
        valid = np.isfinite(closes) & np.isfinite(times)
        closes = closes[valid]
        times = times[valid]
        if len(closes) < 8:
            return {'status': 'insufficient_data', 'model': 'chainlink_1m_nowcast', 'up_probability': None}
        current = float(closes[-1])
        end_dt = self._parse_dt(end_time)
        last_ts = float(times[-1])
        minutes_to_close = max(0.0, min(15.0, (end_dt.timestamp() - last_ts) / 60.0))
        diffs = np.diff(closes)
        recent = diffs[-12:] if len(diffs) >= 12 else diffs
        if len(recent) == 0:
            recent = np.array([0.0])
        weights = np.linspace(1.0, 2.0, len(recent))
        momentum_per_min = float(np.average(recent, weights=weights))
        median_momentum = float(np.median(recent[-5:])) if len(recent) >= 5 else float(np.median(recent))
        blended_momentum = 0.65 * median_momentum + 0.35 * momentum_per_min
        raw_move = blended_momentum * minutes_to_close
        vol_per_min = float(np.std(diffs[-30:])) if len(diffs) >= 2 else 0.0
        recent_range = float(np.max(closes[-15:]) - np.min(closes[-15:])) if len(closes) >= 3 else 0.0
        move_cap = max(2.0, min(35.0, max(vol_per_min * max(minutes_to_close, 1.0) ** 0.5 * 0.75, recent_range * 0.35)))
        predicted = current + max(-move_cap, min(move_cap, raw_move))
        sigma = max(3.0, vol_per_min * max(minutes_to_close, 1.0) ** 0.5, recent_range / 4.0)
        z = (predicted - float(target)) / sigma
        up_probability = 0.5 * (1 + math.erf(z / math.sqrt(2)))
        return {
            'status': 'ok',
            'model': 'chainlink_1m_bounded_nowcast',
            'target_price': round(float(target), 2),
            'current_price': round(current, 2),
            'forecast_price_at_close': round(float(predicted), 2),
            'minutes_to_close': round(minutes_to_close, 2),
            'momentum_per_min': round(blended_momentum, 4),
            'move_cap': round(move_cap, 4),
            'residual_sigma': round(sigma, 4),
            'up_probability': round(max(0, min(1, up_probability)), 4),
        }

    def _trend_probability(self, klines: list[dict], target: float, end_time: str):
        try:
            import numpy as np
            from sklearn.linear_model import HuberRegressor
        except Exception as exc:
            return {'status': 'unavailable', 'up_probability': None, 'error': str(exc)[:160]}
        closes = np.array([row['close'] for row in klines], dtype=float)
        times = np.array([row['timestamp_ms'] / 1000 for row in klines], dtype=float)
        if len(closes) < 30:
            return {'status': 'insufficient_data', 'up_probability': None}
        base = times[0]
        x = ((times - base) / 60).reshape(-1, 1)
        end_ts = self._parse_dt(end_time).timestamp()
        x_end = np.array([[(end_ts - base) / 60]])
        model = HuberRegressor().fit(x, closes)
        predicted = float(model.predict(x_end)[0])
        residuals = closes - model.predict(x)
        sigma = max(float(np.std(residuals)), 1e-6)
        z = (predicted - target) / sigma
        up_probability = 0.5 * (1 + math.erf(z / math.sqrt(2)))
        return {
            'model': 'huber_trend_fallback',
            'target_price': round(target, 2),
            'forecast_price_at_close': round(predicted, 2),
            'residual_sigma': round(sigma, 4),
            'up_probability': round(max(0, min(1, up_probability)), 4),
        }

    def _orderbook_probability(self, market: dict):
        probabilities = {}
        for token in market.get('tokens') or []:
            book = token.get('book') or {}
            bid = self._float_or_none((book.get('best_bid') or {}).get('price'))
            ask = self._float_or_none((book.get('best_ask') or {}).get('price'))
            if bid is not None and ask is not None:
                probabilities[token.get('outcome')] = round((bid + ask) / 2, 4)
        return probabilities

    def _side_probability(self, signal: dict, side: str | None):
        up_probability = (signal.get('prophet') or {}).get('up_probability')
        if isinstance(up_probability, int | float) and side in {'Up', 'Down'}:
            return up_probability if side == 'Up' else 1 - up_probability
        confidence = signal.get('confidence')
        if isinstance(confidence, int | float) and signal.get('preferred_side') == side:
            return confidence
        return None

    def _side_microstructure(self, market: dict, side: str | None):
        for token in market.get('tokens') or []:
            if token.get('outcome') != side:
                continue
            book = token.get('book') or {}
            bid = book.get('best_bid') or {}
            ask = book.get('best_ask') or {}
            bid_price = self._float_or_none(bid.get('price'))
            ask_price = self._float_or_none(ask.get('price'))
            ask_size = self._float_or_none(ask.get('size'))
            spread = ask_price - bid_price if ask_price is not None and bid_price is not None else None
            return {
                'bid': bid_price,
                'ask': ask_price,
                'ask_size': ask_size,
                'spread': round(spread, 4) if spread is not None else None,
                'token_id': token.get('token_id'),
            }
        return {'bid': None, 'ask': None, 'ask_size': None, 'spread': None}

    def _interval_seconds(self, interval) -> int:
        value = str(interval).lower().strip()
        if value in {'15', '15m', '15min', '900'}:
            return 900
        if value in {'5', '5m', '5min', '300'}:
            return 300
        raise ValueError('interval must be 15m or 5m')

    def _parse_dt(self, value: str | None):
        if not value:
            return None
        return datetime.fromisoformat(value.replace('Z', '+00:00'))

    def _btc_updown_deep_train(self, **kwargs):
        """Train a persisted BTC Up/Down 5m sequence model from Chainlink candles.

        The runtime intentionally avoids heavyweight DL frameworks. We use a deterministic
        LSTM-style gated encoder (sigmoid/tanh gates) over 1m returns and train a tanh MLP
        probability head with scikit-learn. The saved artifact is then reusable by finance.
        """
        try:
            import numpy as np
            import pandas as pd
            import joblib
            from pathlib import Path
            from sklearn.dummy import DummyClassifier
            from sklearn.metrics import accuracy_score, log_loss
            from sklearn.model_selection import train_test_split
            from sklearn.neural_network import MLPClassifier
            from sklearn.preprocessing import StandardScaler
        except Exception as exc:
            return {'status': 'unavailable', 'error': f'missing_ml_dependency: {str(exc)[:160]}'}

        interval = str(kwargs.get('interval') or '5m')
        if interval != '5m':
            raise ValueError('btc_updown_deep_train currently supports interval=5m')
        start_et = kwargs.get('window_start_et') or kwargs.get('start_et') or '2026-05-23 23:50:00 EDT'
        end_et = kwargs.get('window_end_et') or kwargs.get('end_et') or '2026-05-23 23:55:00 EDT'
        start_dt = self._parse_et_datetime(start_et)
        end_dt = self._parse_et_datetime(end_et)
        lookback_window = str(kwargs.get('lookback_window') or '1d').lower()
        lookback_minutes = int(kwargs.get('lookback_minutes') or (1440 if lookback_window in {'1d','24h','1day'} else 1440))
        sequence_length = max(8, min(int(kwargs.get('sequence_length') or 30), 180))
        hidden_size = max(4, min(int(kwargs.get('hidden_size') or 16), 64))
        seed = int(kwargs.get('seed') or 42)

        end_time_ms = int(end_dt.timestamp() * 1000) + 60_000
        candle_limit = lookback_minutes + sequence_length + 20
        candles = self._chainlink_klines_extended('1m', candle_limit, end_time_ms=end_time_ms)
        if len(candles) < max(90, sequence_length + 20):
            return {'status': 'insufficient_data', 'candles': len(candles), 'required_min': max(90, sequence_length + 20)}
        samples = self._btc_updown_5m_samples(candles, sequence_length=sequence_length)
        target_sample = self._find_window_sample(samples, start_dt, end_dt)
        train_samples = [x for x in samples if x['end_ts'] <= int(start_dt.timestamp())]
        if len(train_samples) < 40:
            train_samples = samples[:-1] if len(samples) > 1 else samples
        if len(train_samples) < 12:
            return {'status': 'insufficient_samples', 'samples': len(train_samples), 'candles': len(candles)}

        weights = self._lstm_gate_weights(hidden_size=hidden_size, seed=seed)
        X, y, rows = self._samples_to_matrix(train_samples, weights)
        target_X = None
        if target_sample:
            target_X, _, _ = self._samples_to_matrix([target_sample], weights)
        classes = sorted(set(int(v) for v in y.tolist()))
        scaler = StandardScaler()
        Xs = scaler.fit_transform(X)
        metrics = {'classes': classes, 'train_samples': int(len(y)), 'features': int(X.shape[1])}
        if len(classes) < 2:
            clf = DummyClassifier(strategy='most_frequent')
            clf.fit(Xs, y)
            metrics.update({'status': 'single_class_fallback', 'accuracy': 1.0, 'log_loss': None})
        else:
            stratify = y if min(np.bincount(y.astype(int))) >= 2 and len(y) >= 30 else None
            X_train, X_test, y_train, y_test = train_test_split(Xs, y, test_size=0.25, random_state=seed, stratify=stratify)
            clf = MLPClassifier(hidden_layer_sizes=(64, 32), activation='tanh', solver='adam', alpha=0.001, learning_rate_init=0.003, max_iter=900, random_state=seed, early_stopping=True, n_iter_no_change=30)
            clf.fit(X_train, y_train)
            pred = clf.predict(X_test)
            proba = clf.predict_proba(X_test)
            metrics.update({
                'status': 'ok',
                'accuracy': round(float(accuracy_score(y_test, pred)), 4),
                'log_loss': round(float(log_loss(y_test, proba, labels=clf.classes_)), 4) if len(set(y_test.tolist())) > 1 else None,
                'iterations': int(getattr(clf, 'n_iter_', 0) or 0),
                'test_samples': int(len(y_test)),
            })

        model_dir = Path('storage/artifacts/models')
        model_dir.mkdir(parents=True, exist_ok=True)
        model_path = model_dir / 'polymarket_btc_updown_5m_lstm_gate.joblib'
        dataset_path = model_dir / 'polymarket_btc_updown_5m_lstm_gate_dataset.csv'
        metadata_path = model_dir / 'polymarket_btc_updown_5m_lstm_gate_metadata.json'
        artifact = {
            'kind': 'polymarket_btc_updown_5m_lstm_gate',
            'model': clf,
            'scaler': scaler,
            'weights': weights,
            'sequence_length': sequence_length,
            'hidden_size': hidden_size,
            'interval': interval,
            'created_at': datetime.now(UTC).isoformat().replace('+00:00','Z'),
            'feature_note': 'Deterministic sigmoid/tanh LSTM-style gated encoder over 1m returns + tanh MLP probability head.',
        }
        joblib.dump(artifact, model_path)
        pd.DataFrame(rows).to_csv(dataset_path, index=False)
        target_prediction = None
        if target_sample and target_X is not None:
            prob_up = self._classifier_up_probability(clf, scaler.transform(target_X))[0]
            target_prediction = self._sample_public_payload(target_sample)
            target_prediction.update({
                'model_probability_up': round(float(prob_up), 4),
                'model_probability_down': round(float(1 - prob_up), 4),
                'model_side': 'UP' if prob_up >= 0.5 else 'DOWN',
            })
        metadata = {
            'status': metrics.get('status'),
            'model_path': str(model_path),
            'dataset_path': str(dataset_path),
            'metadata_path': str(metadata_path),
            'training_window': {'lookback_window': lookback_window, 'lookback_minutes': lookback_minutes, 'sequence_length': sequence_length},
            'target_window_et': f'{self._format_et(start_dt)} - {self._format_et(end_dt)}',
            'metrics': metrics,
            'target_window': target_prediction,
            'candles': len(candles),
            'samples_total': len(samples),
        }
        metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False))
        return {**metadata, 'urls_exposed': False, 'secrets_exposed': False}

    def _btc_updown_deep_predict(self, **kwargs):
        try:
            import joblib
            from pathlib import Path
        except Exception as exc:
            return {'status': 'unavailable', 'error': f'missing_ml_dependency: {str(exc)[:160]}'}
        model_path = Path(kwargs.get('model_path') or 'storage/artifacts/models/polymarket_btc_updown_5m_lstm_gate.joblib')
        if not model_path.exists():
            return {'status': 'missing_model', 'model_path': str(model_path), 'hint': 'Run action=btc_updown_deep_train first.'}
        artifact = joblib.load(model_path)
        sequence_length = int(artifact.get('sequence_length') or 30)
        end_dt = self._parse_et_datetime(kwargs.get('window_end_et') or kwargs.get('end_et')) if (kwargs.get('window_end_et') or kwargs.get('end_et')) else datetime.now(UTC).astimezone(POLYMARKET_TIMEZONE)
        end_time_ms = int(end_dt.timestamp() * 1000)
        candles = self._chainlink_klines_extended('1m', sequence_length + 12, end_time_ms=end_time_ms)
        samples = self._btc_updown_5m_samples(candles, sequence_length=sequence_length)
        if not samples:
            return {'status': 'insufficient_data', 'candles': len(candles)}
        sample = samples[-1]
        X, _, _ = self._samples_to_matrix([sample], artifact['weights'])
        prob_up = self._classifier_up_probability(artifact['model'], artifact['scaler'].transform(X))[0]
        payload = self._sample_public_payload(sample)
        payload.update({
            'status': 'ok',
            'model_path': str(model_path),
            'model_probability_up': round(float(prob_up), 4),
            'model_probability_down': round(float(1 - prob_up), 4),
            'model_side': 'UP' if prob_up >= 0.5 else 'DOWN',
            'urls_exposed': False,
            'secrets_exposed': False,
        })
        return payload

    def _chainlink_klines_extended(self, interval: str, limit: int, end_time_ms: int | None = None):
        rows = []
        remaining = max(1, min(int(limit), 5000))
        cursor = end_time_ms
        while remaining > 0:
            batch = 60 if remaining >= 60 else (30 if remaining >= 30 else 15)
            candles = self._chainlink_candles(interval, limit=batch, end_time_ms=cursor)
            if not candles:
                break
            rows = candles + rows
            remaining -= len(candles)
            first_time = self._float_or_none(candles[0].get('time'))
            if first_time is None or len(candles) < batch:
                break
            cursor = int(first_time * 1000) - 1
        deduped = {int(row['time']): row for row in rows if row.get('time') is not None}
        out = []
        for ts in sorted(deduped)[-limit:]:
            row = deduped[ts]
            close = self._float_or_none(row.get('close'))
            open_price = self._float_or_none(row.get('open'))
            if close is not None:
                out.append({'timestamp_ms': ts * 1000, 'open': open_price if open_price is not None else close, 'close': close})
        return out

    def _btc_updown_5m_samples(self, candles: list[dict], sequence_length: int):
        by_ts = {int(row['timestamp_ms'] // 1000): row for row in candles if row.get('timestamp_ms') is not None and self._float_or_none(row.get('close')) is not None}
        timestamps = sorted(by_ts)
        samples = []
        if len(timestamps) < sequence_length + 6:
            return samples
        ts_set = set(timestamps)
        for start_ts in timestamps:
            if start_ts % 300 != 0:
                continue
            end_ts = start_ts + 300
            close_ts = end_ts - 60
            if close_ts not in ts_set:
                continue
            prev = [t for t in timestamps if t < start_ts]
            if len(prev) < sequence_length:
                continue
            seq_ts = prev[-sequence_length:]
            closes = [float(by_ts[t]['close']) for t in seq_ts]
            price_to_beat = self._float_or_none(by_ts[start_ts].get('open')) or self._float_or_none(by_ts[start_ts].get('close'))
            close_price = self._float_or_none(by_ts[close_ts].get('close'))
            if price_to_beat is None or close_price is None:
                continue
            returns = []
            for a, b in zip(closes[:-1], closes[1:]):
                returns.append((b - a) / a if a else 0.0)
            while len(returns) < sequence_length:
                returns.insert(0, 0.0)
            movement = close_price - price_to_beat
            start_dt = datetime.fromtimestamp(start_ts, UTC).astimezone(POLYMARKET_TIMEZONE)
            end_dt = datetime.fromtimestamp(end_ts, UTC).astimezone(POLYMARKET_TIMEZONE)
            samples.append({
                'start_ts': start_ts,
                'end_ts': end_ts,
                'start_time_et': self._format_et(start_dt),
                'end_time_et': self._format_et(end_dt),
                'window_et': f'{self._format_et(start_dt)} - {self._format_et(end_dt)}',
                'sequence': returns[-sequence_length:],
                'price_to_beat_reference': float(price_to_beat),
                'close_price': float(close_price),
                'movement_usd': float(movement),
                'winner': 'UP' if movement >= 0 else 'DOWN',
                'label': 1 if movement >= 0 else 0,
            })
        return samples

    def _lstm_gate_weights(self, hidden_size: int, seed: int):
        try:
            import numpy as np
        except Exception as exc:
            raise RuntimeError(str(exc))
        rng = np.random.default_rng(seed)
        scale = 1.0 / max(1.0, hidden_size ** 0.5)
        weights = {'hidden_size': hidden_size, 'seed': seed}
        for gate in ('i','f','o','g'):
            weights[f'W_{gate}'] = rng.normal(0, scale, size=(hidden_size, 1))
            weights[f'U_{gate}'] = rng.normal(0, scale, size=(hidden_size, hidden_size))
            weights[f'b_{gate}'] = np.zeros(hidden_size)
        weights['b_f'] = np.ones(hidden_size) * 0.5
        return weights

    def _samples_to_matrix(self, samples: list[dict], weights: dict):
        import numpy as np
        encoded = self._lstm_gate_encode_batch([s['sequence'] for s in samples], weights)
        extras = []
        rows = []
        for sample in samples:
            seq = np.array(sample['sequence'], dtype=float)
            momentum_5 = float(np.sum(seq[-5:])) if len(seq) >= 5 else float(np.sum(seq))
            momentum_15 = float(np.sum(seq[-15:])) if len(seq) >= 15 else float(np.sum(seq))
            vol = float(np.std(seq)) if len(seq) else 0.0
            last = float(seq[-1]) if len(seq) else 0.0
            extras.append([momentum_5, momentum_15, vol, last])
            rows.append({
                'window_et': sample['window_et'],
                'price_to_beat_reference': round(sample['price_to_beat_reference'], 6),
                'close_price': round(sample['close_price'], 6),
                'movement_usd': round(sample['movement_usd'], 6),
                'winner': sample['winner'],
                'label': sample['label'],
                'momentum_5': momentum_5,
                'momentum_15': momentum_15,
                'volatility': vol,
            })
        X = np.hstack([encoded, np.array(extras, dtype=float)])
        y = np.array([int(s['label']) for s in samples], dtype=int)
        return X, y, rows

    def _lstm_gate_encode_batch(self, sequences: list[list[float]], weights: dict):
        import numpy as np
        def sigmoid(x):
            return 1.0 / (1.0 + np.exp(-np.clip(x, -50, 50)))
        hidden_size = int(weights['hidden_size'])
        outputs = []
        for seq in sequences:
            h = np.zeros(hidden_size)
            c = np.zeros(hidden_size)
            arr = np.array(seq, dtype=float)
            std = float(np.std(arr)) or 1e-6
            arr = (arr - float(np.mean(arr))) / std
            for value in arr:
                x = np.array([value], dtype=float)
                i = sigmoid(weights['W_i'] @ x + weights['U_i'] @ h + weights['b_i'])
                f = sigmoid(weights['W_f'] @ x + weights['U_f'] @ h + weights['b_f'])
                o = sigmoid(weights['W_o'] @ x + weights['U_o'] @ h + weights['b_o'])
                g = np.tanh(weights['W_g'] @ x + weights['U_g'] @ h + weights['b_g'])
                c = f * c + i * g
                h = o * np.tanh(c)
            outputs.append(h)
        return np.array(outputs, dtype=float)

    def _classifier_up_probability(self, clf, X):
        import numpy as np
        if hasattr(clf, 'predict_proba'):
            proba = clf.predict_proba(X)
            classes = list(getattr(clf, 'classes_', []))
            if 1 in classes:
                return proba[:, classes.index(1)]
            return np.zeros(X.shape[0])
        return np.asarray(clf.predict(X), dtype=float)

    def _find_window_sample(self, samples: list[dict], start_dt: datetime, end_dt: datetime):
        start_ts = int(start_dt.astimezone(UTC).timestamp())
        end_ts = int(end_dt.astimezone(UTC).timestamp())
        return next((s for s in samples if int(s['start_ts']) == start_ts and int(s['end_ts']) == end_ts), None)

    def _sample_public_payload(self, sample: dict):
        return {
            'window_et': sample['window_et'],
            'price_to_beat_reference': round(sample['price_to_beat_reference'], 2),
            'close_price': round(sample['close_price'], 2),
            'movement_usd': round(sample['movement_usd'], 2),
            'winner': sample['winner'],
        }

    def _parse_et_datetime(self, value: str | None) -> datetime:
        if not value:
            return datetime.now(UTC).astimezone(POLYMARKET_TIMEZONE)
        text = str(value).strip()
        text = re.sub(r'\s+(EDT|EST)$', '', text, flags=re.IGNORECASE)
        for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M'):
            try:
                return datetime.strptime(text, fmt).replace(tzinfo=POLYMARKET_TIMEZONE)
            except ValueError:
                pass
        parsed = self._parse_dt(str(value))
        if parsed:
            return parsed.astimezone(POLYMARKET_TIMEZONE)
        raise ValueError(f'Invalid ET datetime: {value}')

    def _format_countdown(self, seconds: int) -> str:
        minutes, secs = divmod(max(0, int(seconds)), 60)
        return f'{minutes:02d}:{secs:02d}'

    def _format_et(self, value: datetime) -> str:
        return value.strftime('%Y-%m-%d %H:%M:%S %Z')

    def _float_or_none(self, value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _outcome_tokens(self, market: dict) -> list[dict]:
        outcomes = market.get('outcomes') or []
        token_ids = market.get('clob_token_ids') or []
        if isinstance(outcomes, str):
            outcomes = self._maybe_json(outcomes)
        if isinstance(token_ids, str):
            token_ids = self._maybe_json(token_ids)
        return [
            {'outcome': str(outcome), 'token_id': str(token_id)}
            for outcome, token_id in zip(outcomes or [], token_ids or [])
        ]

    def _market_summary(self, m: dict, detail: bool = False):
        tokens = self._maybe_json(m.get('clobTokenIds') or m.get('clob_token_ids') or m.get('tokens') or [])
        outcomes = self._maybe_json(m.get('outcomes') or [])
        item = {
            'id': m.get('id'),
            'question': m.get('question') or m.get('title') or m.get('slug'),
            'slug': m.get('slug'),
            'active': m.get('active'),
            'closed': m.get('closed'),
            'end_date': m.get('endDate') or m.get('end_date'),
            'liquidity': m.get('liquidity') or m.get('liquidityNum'),
            'volume': m.get('volume') or m.get('volumeNum'),
            'outcomes': outcomes,
            'clob_token_ids': tokens,
        }
        if detail:
            item['description'] = m.get('description')
            item['condition_id'] = m.get('conditionId') or m.get('condition_id')
            item['raw_keys'] = sorted(list(m.keys()))[:80]
        return item

    def _maybe_json(self, value):
        if isinstance(value, str):
            try:
                return json.loads(value)
            except Exception:
                return value
        return value

    def _book_summary(self, raw):
        bids = raw.get('bids') or raw.get('buy') or [] if isinstance(raw, dict) else []
        asks = raw.get('asks') or raw.get('sell') or [] if isinstance(raw, dict) else []

        def normalized(levels):
            rows = []
            for x in levels:
                if isinstance(x, dict):
                    rows.append({'price': x.get('price'), 'size': x.get('size')})
                elif isinstance(x, (list, tuple)) and len(x) >= 2:
                    rows.append({'price': x[0], 'size': x[1]})
            return rows

        def as_float(value):
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        def top(levels, reverse=False):
            levels = normalized(levels)
            if not levels:
                return None
            return sorted(levels, key=lambda x: as_float(x['price']) if as_float(x['price']) is not None else -1, reverse=reverse)[0]

        return {'best_bid': top(bids, reverse=True), 'best_ask': top(asks), 'bids_count': len(bids), 'asks_count': len(asks)}
