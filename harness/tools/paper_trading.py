from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from config import settings

from .base import BaseTool
from .mexc_spot import MexcSpotTool
from .polymarket import PolymarketTool


class PaperTradingTool(BaseTool):
    name = 'paper_trading'
    default_rules = {
        'mexc_spot': {
            'buy': ['RSI<=30', 'MACD histogram<0', 'price<VWAP'],
            'sell': ['RSI>=70', 'MACD histogram>0', 'price>VWAP'],
            'filters': ['volume_ratio', 'EMA50', 'Bollinger bands', 'ATR/risk'],
        },
        'polymarket_btc_updown': {
            'trade': ['manual_enabled=true', 'mode in observe/paper/live', 'confidence>=0.80', 'edge>=0.03', 'spread<=0.08', 'ask_size>=1', 'seconds_to_close>=60', 'one_trade_per_event_window'],
            'risk': ['stake fixed manually at 1/2/3 USDT', 'max one trade per 5m/15m window', 'live requires server flag plus UI enablement', 'price_to_beat=Chainlink candle open'],
            'exit': ['stop_loss_when_position_value_drawdown<=-8.34pct (3.00 -> 2.75 USDT)', 'take_profit_when_position_value_gain>=100pct (3.00 -> 6.00 USDT)', 'manual_liquidation_button_per_trade', 'auto_claim_redeemable_profit_when_claim_relayer_configured'],
        },
        'modes': {
            'observe': 'analiza señales y registra transacciones observadas sin simular orden',
            'paper': 'simula órdenes y registra auditoría; no toca exchanges reales',
            'live': 'requiere habilitación explícita, SDK CLOB y credenciales Polymarket',
        },
    }

    def run(self, action: str | None = None, role: str | None = None, **kwargs):
        if role not in {'admin', 'trader'}:
            raise PermissionError('paper_trading is only allowed for admin/trader roles')
        action = action or kwargs.pop('task', None) or 'run_cycle'
        if action == 'status':
            return self._status()
        if action == 'rules':
            return {'rules': self.default_rules, 'live_execution_enabled': settings.polymarket_live_trading_enabled}
        if action == 'run_cycle':
            return self._run_cycle(role=role, **kwargs)
        raise ValueError('unsupported paper_trading action')

    def _run_cycle(self, role: str | None, **kwargs):
        mode = str(kwargs.get('mode') or 'paper').lower()
        if mode not in {'observe', 'paper', 'live'}:
            raise ValueError('mode must be observe, paper or live')
        if mode == 'live' and not (settings.polymarket_live_trading_enabled and kwargs.get('live_execution_enabled') is True):
            raise PermissionError('MODE=live is blocked; set POLYMARKET_LIVE_TRADING_ENABLED=true and live_execution_enabled=True')
        venues = kwargs.get('venues') or ['polymarket', 'mexc']
        if isinstance(venues, str):
            venues = [x.strip().lower() for x in venues.split(',') if x.strip()]
        bankroll = float(kwargs.get('bankroll_usdt') or 10000)
        polymarket_stake_usdt = max(0, float(kwargs.get('polymarket_stake_usdt') or 1))
        if polymarket_stake_usdt not in {1.0, 2.0, 3.0}:
            polymarket_stake_usdt = min({1.0, 2.0, 3.0}, key=lambda value: abs(value - polymarket_stake_usdt))
        max_stake_pct = min(max(float(kwargs.get('max_stake_pct') or 0.05), 0), 0.05)
        kelly_fraction = min(max(float(kwargs.get('kelly_fraction') or 0.25), 0), 0.25)
        if mode == 'live' and 'mexc' in venues:
            venues = [venue for venue in venues if venue != 'mexc']
        threshold = float(kwargs.get('threshold') or 0.8)
        result = {
            'mode': mode,
            'cycle_id': datetime.now(UTC).strftime('%Y%m%dT%H%M%S.%fZ'),
            'created_at': datetime.now(UTC).isoformat().replace('+00:00', 'Z'),
            'bankroll_usdt': bankroll,
            'max_stake_pct': max_stake_pct,
            'polymarket_stake_usdt': polymarket_stake_usdt,
            'kelly_fraction': kelly_fraction,
            'polymarket_auto_liquidate_enabled': self._bool_setting(kwargs.get('polymarket_auto_liquidate_enabled'), True),
            'polymarket_stop_loss_pct': float(kwargs.get('polymarket_stop_loss_pct') or os.getenv('POLYMARKET_STOP_LOSS_PCT', '-8.34') or -8.34),
            'polymarket_take_profit_pct': float(kwargs.get('polymarket_take_profit_pct') or os.getenv('POLYMARKET_TAKE_PROFIT_PCT', '100') or 100),
            'rules': kwargs.get('rules') or self.default_rules,
            'transactions': [],
            'orders': [],
            'position_actions': [],
            'claim_actions': [],
            'observations': [],
            'errors': [],
            'secret_exposed': False,
        }
        result['_existing_polymarket_trade_keys'] = self._existing_polymarket_trade_keys()
        if 'polymarket' in venues:
            if mode == 'live':
                self._manage_polymarket_live_positions(result, kwargs)
            self._polymarket_cycle(result, role, bankroll, max_stake_pct, threshold, polymarket_stake_usdt, kwargs)
        if 'mexc' in venues:
            self._mexc_cycle(result, role, kwargs)
        result['orders_count'] = len(result['orders'])
        result['position_actions_count'] = len(result['position_actions'])
        result['claim_actions_count'] = len(result['claim_actions'])
        result['observations_count'] = len(result['observations'])
        result['transactions_count'] = len(result['transactions'])
        result.pop('_existing_polymarket_trade_keys', None)
        result['audit_path'] = self._append_audit(result)
        return result

    def _polymarket_cycle(self, result, role, bankroll, max_stake_pct, threshold, polymarket_stake_usdt, kwargs):
        try:
            payload = PolymarketTool().run(
                action='btc_updown_5m15m_coordinated_signal',
                role=role,
                asset='btc',
                threshold=threshold,
                candle_interval=kwargs.get('candle_interval') or '5m',
                lookback_window=kwargs.get('lookback_window') or '1d',
                lookback=int(kwargs.get('lookback') or 288),
                prediction_candle_interval=kwargs.get('prediction_candle_interval') or '1m',
                prediction_lookback=int(kwargs.get('prediction_lookback') or 90),
                min_edge=float(kwargs.get('polymarket_min_edge') or 0.03),
                max_spread=float(kwargs.get('polymarket_max_spread') or 0.08),
                min_ask_size=float(kwargs.get('polymarket_min_ask_size') or 1),
                min_seconds_to_close=int(kwargs.get('polymarket_min_seconds_to_close') or 45),
            )
        except Exception as exc:
            result['errors'].append({'venue': 'polymarket', 'error': str(exc)[:300]})
            return
        if 'candidates' in payload:
            self._polymarket_coordinated_cycle(result, payload, bankroll, max_stake_pct, polymarket_stake_usdt)
            return
        markets = {m.get('interval'): m for m in payload.get('markets') or []}
        for signal in payload.get('signals') or []:
            market = markets.get(signal.get('interval')) or {}
            trade = self._polymarket_trade(signal, market, threshold)
            base = {
                'venue': 'polymarket',
                'market': 'BTC Up/Down',
                'interval': signal.get('interval'),
                'window_et': f"{signal.get('start_time_et')} - {signal.get('end_time_et')}",
                'countdown': signal.get('countdown'),
                'probability': signal.get('confidence'),
                'preferred_side': signal.get('preferred_side'),
                'liquidity': market.get('liquidity'),
                'reason': trade['reason'],
            }
            if trade['side'] == 'NONE':
                result['observations'].append({**base, 'signal': 'NO TRADE'})
                continue
            stake = min(polymarket_stake_usdt, bankroll * max_stake_pct, bankroll * trade['fractional_kelly'])
            if stake <= 0:
                result['observations'].append({**base, 'signal': 'NO TRADE', 'reason': 'Kelly<=0'})
                continue
            if self._polymarket_trade_exists(result, signal.get('interval'), base.get('window_et')):
                result['observations'].append({**base, 'signal': 'NO TRADE', 'reason': 'duplicate_window_trade'})
                continue
            order = {
                **base,
                'paper': result['mode'] == 'paper',
                'mode': result['mode'],
                'side': trade['side'],
                'price': trade['ask'],
                'full_kelly': round(trade['full_kelly'], 6),
                'fractional_kelly': round(trade['fractional_kelly'], 6),
                'stake_usdt': round(stake, 2),
                'max_loss_usdt': round(stake, 2),
                'execution': self._execution_label(result['mode']),
                'token_id': trade.get('token_id'),
            }
            self._execute_polymarket_live_if_needed(result, order, base)
            result['orders'].append(order)
            result['transactions'].append(self._transaction(
                result,
                venue='polymarket',
                market='BTC Up/Down',
                symbol='BTC',
                side=trade['side'],
                status='observed' if result['mode'] == 'observe' else order.get('transaction_status', 'accepted'),
                price=trade['ask'],
                stake_usdt=stake if result['mode'] != 'observe' else 0,
                confidence=base.get('probability'),
                kelly=trade['fractional_kelly'],
                risk=trade['reason'],
                interval=signal.get('interval'),
                window=base.get('window_et'),
            ))

    def _polymarket_coordinated_cycle(self, result, payload, bankroll, max_stake_pct, polymarket_stake_usdt):
        candidates = payload.get('candidates') or []
        strategy = payload.get('strategy') or 'BTC Up/Down independent 5m/15m live signal'
        tradable_by_interval = {}
        for candidate in candidates:
            interval = str(candidate.get('interval') or '').lower()
            if interval in {'5m', '15m'} and candidate.get('passes_filters') and interval not in tradable_by_interval:
                tradable_by_interval[interval] = candidate
        tradable = [tradable_by_interval[key] for key in ('5m', '15m') if key in tradable_by_interval]
        if not tradable:
            result['observations'].append({
                'venue': 'polymarket',
                'market': 'BTC Up/Down',
                'strategy': strategy,
                'signal': 'NO TRADE',
                'reason': ', '.join(payload.get('reasons') or ['no_event_passed_filters']),
                'candidates': candidates,
                'filters': payload.get('filters') or {},
            })
            result['transactions'].append(self._transaction(
                result,
                strategy=strategy,
                venue='polymarket',
                market='BTC Up/Down',
                symbol='BTC',
                side='NONE',
                status='no_trade',
                stake_usdt=0,
                confidence=max([c.get('confidence') or 0 for c in candidates] or [0]),
                risk=', '.join(payload.get('reasons') or ['no_event_passed_filters']),
                indicators={'candidates': candidates, 'filters': payload.get('filters') or {}},
            ))
            return
        per_window_budget = min(polymarket_stake_usdt, bankroll * max_stake_pct)
        kelly_fraction_setting = result.get('kelly_fraction', 0.25)
        for candidate in tradable:
            side = 'UP' if candidate.get('preferred_side') == 'Up' else ('DOWN' if candidate.get('preferred_side') == 'Down' else 'NONE')
            ask = self._float((candidate.get('microstructure') or {}).get('ask'))
            probability = self._float(candidate.get('probability') or candidate.get('confidence'))
            full_kelly = self._kelly_fraction(probability, ask)
            fractional_kelly = max(0, min(max_stake_pct, full_kelly * kelly_fraction_setting))
            stake = min(per_window_budget, bankroll * fractional_kelly)
            base = {
                'venue': 'polymarket',
                'market': 'BTC Up/Down',
                'strategy': strategy,
                'interval': candidate.get('interval'),
                'window_et': candidate.get('window_et'),
                'countdown': candidate.get('countdown'),
                'probability': probability,
                'preferred_side': candidate.get('preferred_side'),
                'price_to_beat_reference': candidate.get('price_to_beat_reference'),
                'current_price_reference': candidate.get('current_price_reference'),
                'forecast_price_at_close': candidate.get('forecast_price_at_close'),
                'reason': 'evento independiente pasa confianza, edge, Kelly y microestructura',
                'filters': payload.get('filters') or {},
                'candidate': candidate,
            }
            if side == 'NONE':
                result['observations'].append({**base, 'signal': 'NO TRADE', 'reason': 'missing_side'})
                continue
            if stake <= 0:
                result['observations'].append({**base, 'signal': 'NO TRADE', 'reason': 'kelly_or_stake_zero'})
                continue
            if self._polymarket_trade_exists(result, candidate.get('interval'), candidate.get('window_et')):
                result['observations'].append({**base, 'signal': 'NO TRADE', 'reason': 'duplicate_window_trade'})
                continue
            order = {
                **base,
                'paper': result['mode'] == 'paper',
                'mode': result['mode'],
                'side': side,
                'price': ask,
                'edge': candidate.get('edge'),
                'full_kelly': round(full_kelly, 6),
                'fractional_kelly': round(fractional_kelly, 6),
                'stake_usdt': round(stake, 2),
                'max_loss_usdt': round(stake, 2),
                'execution': self._execution_label(result['mode']),
                'token_id': (candidate.get('microstructure') or {}).get('token_id'),
            }
            self._execute_polymarket_live_if_needed(result, order, base)
            result['orders'].append(order)
            result['transactions'].append(self._transaction(
                result,
                strategy=strategy,
                venue='polymarket',
                market='BTC Up/Down',
                symbol='BTC',
                side=side,
                status='observed' if result['mode'] == 'observe' else order.get('transaction_status', 'accepted'),
                price=ask,
                stake_usdt=stake if result['mode'] != 'observe' else 0,
                confidence=probability,
                kelly=fractional_kelly,
                risk=base['reason'],
                interval=candidate.get('interval'),
                window=candidate.get('window_et'),
                indicators={
                    'candidate': candidate,
                    'filters': payload.get('filters') or {},
                    'price_to_beat_reference': candidate.get('price_to_beat_reference'),
                    'current_price_reference': candidate.get('current_price_reference'),
                    'forecast_price_at_close': candidate.get('forecast_price_at_close'),
                    'full_kelly': round(full_kelly, 6),
                },
            ))


    def _execute_polymarket_live_if_needed(self, result: dict, order: dict, base: dict) -> None:
        if result.get('mode') != 'live':
            return
        token_id = order.get('token_id')
        if not token_id:
            order['execution'] = 'live_rejected_missing_token_id'
            order['transaction_status'] = 'rejected'
            result['errors'].append({'venue': 'polymarket', 'error': 'missing token_id for live execution'})
            return
        try:
            execution = self._place_polymarket_market_buy(
                token_id=str(token_id),
                amount_usdt=float(order.get('stake_usdt') or 0),
                worst_price=float(order.get('price') or 0),
            )
        except Exception as exc:
            order['execution'] = 'live_order_failed'
            order['transaction_status'] = 'rejected'
            order['execution_error'] = str(exc)[:300]
            result['errors'].append({'venue': 'polymarket', 'error': str(exc)[:300], 'interval': base.get('interval')})
            return
        order['execution'] = 'live_order_sent'
        order['transaction_status'] = 'accepted'
        order['execution_result'] = execution


    def _manage_polymarket_live_positions(self, result: dict, kwargs: dict) -> None:
        if not settings.polymarket_live_trading_enabled:
            return
        auto_liquidate = self._bool_setting(kwargs.get('polymarket_auto_liquidate_enabled'), True)
        auto_claim = self._bool_setting(kwargs.get('polymarket_auto_claim_enabled'), True)
        take_profit_pct = float(kwargs.get('polymarket_take_profit_pct') or os.getenv('POLYMARKET_TAKE_PROFIT_PCT', '100') or 100)
        stop_loss_pct = float(kwargs.get('polymarket_stop_loss_pct') or os.getenv('POLYMARKET_STOP_LOSS_PCT', '-8.34') or -8.34)
        try:
            positions = self._fetch_polymarket_positions()
        except Exception as exc:
            result['errors'].append({'venue': 'polymarket', 'error': f'position scan failed: {str(exc)[:240]}'})
            return
        for position in positions:
            if not self._is_managed_polymarket_position(position):
                continue
            summary = self._polymarket_position_summary(position)
            percent_pnl = self._float(position.get('percentPnl'))
            cash_pnl = self._float(position.get('cashPnl'))
            redeemable = bool(position.get('redeemable'))
            if redeemable and auto_claim and cash_pnl > 0:
                action = {**summary, 'action': 'claim_profit', 'cash_pnl': round(cash_pnl, 4)}
                action.update(self._claim_polymarket_position_if_configured(position))
                result['claim_actions'].append(action)
                continue
            exit_reason = None
            if percent_pnl >= take_profit_pct:
                exit_reason = 'take_profit'
            elif percent_pnl <= stop_loss_pct:
                exit_reason = 'stop_loss'
            if not auto_liquidate or redeemable or exit_reason is None:
                continue
            shares = self._float(position.get('size'))
            current_price = self._float(position.get('curPrice'))
            if shares <= 0 or current_price <= 0:
                result['position_actions'].append({
                    **summary,
                    'action': f'liquidate_{exit_reason}',
                    'status': 'skipped_invalid_position_size_or_price',
                    'percent_pnl': round(percent_pnl, 4),
                    'threshold_pct': round(take_profit_pct if exit_reason == 'take_profit' else stop_loss_pct, 4),
                })
                continue
            try:
                execution = self._place_polymarket_market_sell(
                    token_id=str(position.get('asset') or ''),
                    shares=shares,
                    current_price=current_price,
                )
                result['position_actions'].append({
                    **summary,
                    'action': f'liquidate_{exit_reason}',
                    'status': f'{exit_reason}_order_sent',
                    'percent_pnl': round(percent_pnl, 4),
                    'threshold_pct': round(take_profit_pct if exit_reason == 'take_profit' else stop_loss_pct, 4),
                    'cash_pnl': round(cash_pnl, 4),
                    'execution_result': execution,
                })
            except Exception as exc:
                result['position_actions'].append({
                    **summary,
                    'action': f'liquidate_{exit_reason}',
                    'status': f'{exit_reason}_order_failed',
                    'percent_pnl': round(percent_pnl, 4),
                    'threshold_pct': round(take_profit_pct if exit_reason == 'take_profit' else stop_loss_pct, 4),
                    'cash_pnl': round(cash_pnl, 4),
                    'error': str(exc)[:300],
                })
                result['errors'].append({'venue': 'polymarket', 'error': f'{exit_reason} failed: {str(exc)[:240]}'})

    def _fetch_polymarket_positions(self) -> list[dict]:
        user = settings.polymarket_funder_address or os.getenv('FUNDER_ADDRESS', '')
        if not user:
            raise PermissionError('Polymarket funder address is not configured')
        params = urllib.parse.urlencode({
            'user': user,
            'limit': 250,
            'sizeThreshold': 0,
        })
        url = f'https://data-api.polymarket.com/positions?{params}'
        req = urllib.request.Request(url, headers={'accept': 'application/json', 'user-agent': 'quantlabs-paper-trading/1.0'})
        with urllib.request.urlopen(req, timeout=12) as resp:
            payload = json.loads(resp.read().decode('utf-8'))
        if isinstance(payload, list):
            return [row for row in payload if isinstance(row, dict)]
        if isinstance(payload, dict):
            data = payload.get('data') or payload.get('positions') or []
            return [row for row in data if isinstance(row, dict)]
        return []

    def _is_managed_polymarket_position(self, position: dict) -> bool:
        text = ' '.join(str(position.get(key) or '') for key in ('title', 'slug', 'eventSlug', 'marketSlug')).lower()
        if not text.strip():
            return False
        return ('bitcoin' in text or 'btc' in text) and ('up' in text or 'down' in text)

    def _polymarket_position_summary(self, position: dict) -> dict:
        return {
            'venue': 'polymarket',
            'market': position.get('title') or position.get('slug'),
            'outcome': position.get('outcome'),
            'asset': position.get('asset'),
            'condition_id': position.get('conditionId'),
            'size': round(self._float(position.get('size')), 6),
            'avg_price': round(self._float(position.get('avgPrice')), 4),
            'current_price': round(self._float(position.get('curPrice')), 4),
            'current_value': round(self._float(position.get('currentValue')), 4),
            'redeemable': bool(position.get('redeemable')),
            'secret_exposed': False,
        }

    def _claim_polymarket_position_if_configured(self, position: dict) -> dict:
        claim_url = (os.getenv('POLYMARKET_CLAIM_HTTP_URL') or '').strip().rstrip('/')
        if not claim_url:
            return {
                'status': 'claim_ready_relayer_not_configured',
                'note': 'CLOB SDK has no redeem/claim method; configure POLYMARKET_CLAIM_HTTP_URL to enable automatic claiming.',
                'secret_exposed': False,
            }
        body = json.dumps({
            'user': settings.polymarket_funder_address or os.getenv('FUNDER_ADDRESS', ''),
            'asset': position.get('asset'),
            'conditionId': position.get('conditionId'),
            'size': position.get('size'),
        }).encode('utf-8')
        req = urllib.request.Request(
            claim_url,
            data=body,
            headers={'content-type': 'application/json', 'accept': 'application/json'},
            method='POST',
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                payload = json.loads(resp.read().decode('utf-8') or '{}')
            return {'status': 'claim_request_sent', 'claim_result': payload, 'secret_exposed': False}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode('utf-8', errors='replace')[:240]
            return {'status': 'claim_request_failed', 'error': f'HTTP {exc.code}: {detail}', 'secret_exposed': False}
        except Exception as exc:
            return {'status': 'claim_request_failed', 'error': str(exc)[:240], 'secret_exposed': False}

    def _place_polymarket_market_sell(self, token_id: str, shares: float, current_price: float) -> dict:
        if not token_id:
            raise ValueError('token_id is required')
        if shares <= 0:
            raise ValueError('shares must be positive')
        if current_price <= 0:
            raise ValueError('current_price must be positive')
        if not settings.polymarket_live_trading_enabled:
            raise PermissionError('POLYMARKET_LIVE_TRADING_ENABLED is false')
        private_key = settings.polymarket_private_key or os.getenv('PRIVATE_KEY', '')
        funder = settings.polymarket_funder_address or os.getenv('FUNDER_ADDRESS', '')
        if not private_key or not funder:
            raise PermissionError('Polymarket private key/funder address are not configured')
        try:
            from py_clob_client_v2 import ClobClient, MarketOrderArgsV2, OrderType, PartialCreateOrderOptions, Side
        except Exception as exc:
            raise RuntimeError('py-clob-client-v2 is required for Polymarket live orders') from exc
        host = (settings.clob_api or os.getenv('CLOB_API') or 'https://clob.polymarket.com').strip().strip('"').rstrip('/')
        client = ClobClient(
            host=host,
            chain_id=settings.polymarket_chain_id,
            key=private_key,
            signature_type=settings.polymarket_signature_type,
            funder=funder,
            use_server_time=True,
            retry_on_error=True,
        )
        client.set_api_creds(client.derive_api_key())
        tick_size = client.get_tick_size(token_id)
        neg_risk = client.get_neg_risk(token_id)
        slippage = max(0, float(os.getenv('POLYMARKET_MARKET_SELL_SLIPPAGE', '0.05') or 0.05))
        limit_price = max(0.01, min(0.99, current_price - slippage))
        response = client.create_and_post_market_order(
            MarketOrderArgsV2(
                token_id=token_id,
                side=Side.SELL,
                amount=round(shares, 6),
                price=round(limit_price, 2),
                order_type=OrderType.FAK,
            ),
            PartialCreateOrderOptions(tick_size=tick_size, neg_risk=bool(neg_risk)),
            OrderType.FAK,
        )
        return {
            'order_id': response.get('orderID') or response.get('order_id'),
            'status': response.get('status'),
            'success': response.get('success'),
            'shares': round(shares, 6),
            'current_price': round(current_price, 4),
            'limit_price': round(limit_price, 2),
            'secret_exposed': False,
        }

    def _place_polymarket_market_buy(self, token_id: str, amount_usdt: float, worst_price: float) -> dict:
        if amount_usdt <= 0:
            raise ValueError('amount_usdt must be positive')
        if not settings.polymarket_live_trading_enabled:
            raise PermissionError('POLYMARKET_LIVE_TRADING_ENABLED is false')
        private_key = settings.polymarket_private_key or os.getenv('PRIVATE_KEY', '')
        funder = settings.polymarket_funder_address or os.getenv('FUNDER_ADDRESS', '')
        if not private_key or not funder:
            raise PermissionError('Polymarket private key/funder address are not configured')
        try:
            from py_clob_client_v2 import ClobClient, MarketOrderArgsV2, OrderType, PartialCreateOrderOptions, Side
        except Exception as exc:
            raise RuntimeError('py-clob-client-v2 is required for Polymarket live orders') from exc
        host = (settings.clob_api or os.getenv('CLOB_API') or 'https://clob.polymarket.com').strip().strip('"').rstrip('/')
        client = ClobClient(
            host=host,
            chain_id=settings.polymarket_chain_id,
            key=private_key,
            signature_type=settings.polymarket_signature_type,
            funder=funder,
            use_server_time=True,
            retry_on_error=True,
        )
        client.set_api_creds(client.derive_api_key())
        tick_size = client.get_tick_size(token_id)
        neg_risk = client.get_neg_risk(token_id)
        slippage = max(0, float(os.getenv('POLYMARKET_MARKET_BUY_SLIPPAGE', '0.05') or 0.05))
        limit_price = min(0.99, max(0.01, worst_price + slippage))
        response = client.create_and_post_market_order(
            MarketOrderArgsV2(
                token_id=token_id,
                side=Side.BUY,
                amount=round(amount_usdt, 2),
                price=round(limit_price, 2),
                order_type=OrderType.FAK,
            ),
            PartialCreateOrderOptions(tick_size=tick_size, neg_risk=bool(neg_risk)),
            OrderType.FAK,
        )
        return {
            'order_id': response.get('orderID') or response.get('order_id'),
            'status': response.get('status'),
            'success': response.get('success'),
            'amount_usdt': round(amount_usdt, 2),
            'signal_ask_price': round(worst_price, 2),
            'limit_price': round(limit_price, 2),
            'secret_exposed': False,
        }

    def _mexc_cycle(self, result, role, kwargs):
        tickers = kwargs.get('mexc_tickers') or kwargs.get('tickers') or []
        if isinstance(tickers, str):
            tickers = [x.strip() for x in tickers.split(',') if x.strip()]
        if not tickers:
            result['observations'].append({'venue': 'mexc', 'signal': 'NO SCAN', 'reason': 'sin tickers definidos'})
            return
        try:
            payload = MexcSpotTool().run(
                action='scan_spot_long_candidates',
                role=role,
                tickers=tickers,
                interval=kwargs.get('mexc_interval') or '15m',
                limit=int(kwargs.get('mexc_limit') or 200),
            )
        except Exception as exc:
            result['errors'].append({'venue': 'mexc', 'error': str(exc)[:300]})
            return
        for row in payload.get('results') or []:
            signal = row.get('signal') or 'NONE'
            record = {
                'venue': 'mexc',
                'mode': result['mode'],
                'symbol': row.get('symbol'),
                'signal': signal,
                'price': row.get('price'),
                'rsi': row.get('rsi'),
                'macd_histogram': row.get('macd_histogram'),
                'vwap': row.get('vwap'),
                'risk': row.get('risk'),
                'rule_evaluation': self._mexc_rule_evaluation(row),
                'execution': self._execution_label(result['mode']),
            }
            if signal in {'BUY', 'SELL'}:
                result['orders'].append({'paper': result['mode'] == 'paper', **record})
                result['transactions'].append(self._transaction(
                    result,
                    venue='mexc',
                    market='MEXC Spot',
                    symbol=row.get('symbol'),
                    side=signal,
                    status='observed' if result['mode'] == 'observe' else 'accepted',
                    price=row.get('price'),
                    stake_usdt=0,
                    confidence=self._mexc_confidence(row, signal),
                    risk=row.get('risk'),
                    interval=kwargs.get('mexc_interval') or '15m',
                    indicators={
                        'rsi': row.get('rsi'),
                        'macd_histogram': row.get('macd_histogram'),
                        'vwap': row.get('vwap'),
                        'volume_ratio': row.get('volume_ratio'),
                    },
                    rule_evaluation=record['rule_evaluation'],
                ))
            else:
                result['observations'].append(record)
                result['transactions'].append(self._transaction(
                    result,
                    venue='mexc',
                    market='MEXC Spot',
                    symbol=row.get('symbol'),
                    side='NONE',
                    status='no_trade',
                    price=row.get('price'),
                    stake_usdt=0,
                    confidence=max(self._mexc_confidence(row, 'BUY'), self._mexc_confidence(row, 'SELL')),
                    risk=row.get('risk'),
                    interval=kwargs.get('mexc_interval') or '15m',
                    indicators={
                        'rsi': row.get('rsi'),
                        'macd_histogram': row.get('macd_histogram'),
                        'vwap': row.get('vwap'),
                        'volume_ratio': row.get('volume_ratio'),
                    },
                    rule_evaluation=record['rule_evaluation'],
                ))

    def _bool_setting(self, value, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {'1', 'true', 'yes', 'y', 'on'}

    def _polymarket_trade_key(self, interval, window) -> str | None:
        interval = str(interval or '').lower().strip()
        window = str(window or '').strip()
        if not interval or not window:
            return None
        return f"polymarket|btc-up-down|{interval}|{window}"

    def _polymarket_trade_exists(self, result: dict, interval, window) -> bool:
        key = self._polymarket_trade_key(interval, window)
        if not key:
            return False
        existing = result.setdefault('_existing_polymarket_trade_keys', set())
        if key in existing:
            return True
        existing.add(key)
        return False

    def _existing_polymarket_trade_keys(self) -> set[str]:
        root = Path(settings.artifact_root) / 'paper_trading'
        keys: set[str] = set()
        files = sorted(root.glob('*.jsonl'))[-3:] if root.exists() else []
        for path in files:
            try:
                lines = path.read_text(encoding='utf-8').splitlines()
            except OSError:
                continue
            for line in lines[-600:]:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                for tx in record.get('transactions') or []:
                    if not isinstance(tx, dict):
                        continue
                    if str(tx.get('venue') or '').lower() != 'polymarket':
                        continue
                    if str(tx.get('side') or '').upper() not in {'UP', 'DOWN'}:
                        continue
                    if str(tx.get('status') or '').lower() in {'rejected', 'error', 'no_trade'}:
                        continue
                    if float(tx.get('stake_usdt') or 0) <= 0:
                        continue
                    key = self._polymarket_trade_key(tx.get('interval'), tx.get('window') or tx.get('window_et'))
                    if key:
                        keys.add(key)
        return keys

    def _transaction(self, result, **kwargs):
        return {
            'id': f"{result.get('cycle_id')}-{len(result.get('transactions') or [])}",
            'timestamp': result.get('created_at'),
            'agent': 'paper_trading',
            'mode': result.get('mode', 'paper'),
            'strategy': kwargs.get('strategy') or 'Universal Paper Trading Runner',
            'venue': kwargs.get('venue'),
            'market': kwargs.get('market'),
            'symbol': kwargs.get('symbol'),
            'side': kwargs.get('side'),
            'status': kwargs.get('status') or 'observed',
            'price': self._float(kwargs.get('price')) or 0,
            'stake_usdt': round(float(kwargs.get('stake_usdt') or 0), 2),
            'confidence': self._confidence_percent(kwargs.get('confidence')),
            'kelly': self._float(kwargs.get('kelly')) or 0,
            'pnl': 0,
            'execution': self._execution_label(result.get('mode', 'paper')),
            'risk': kwargs.get('risk'),
            'interval': kwargs.get('interval'),
            'window': kwargs.get('window'),
            'indicators': kwargs.get('indicators') or {},
            'rule_evaluation': kwargs.get('rule_evaluation') or {},
        }

    def _execution_label(self, mode: str):
        if mode == 'observe':
            return 'observe_only'
        if mode == 'live':
            return 'live_enabled'
        return 'simulated_only'

    def _mexc_rule_evaluation(self, row: dict):
        return {
            'buy': {
                'rsi<=30': bool((row.get('rsi') or 999) <= 30),
                'macd_histogram<0': bool((row.get('macd_histogram') or 0) < 0),
                'price<vwap': bool(row.get('price_below_vwap')),
            },
            'sell': {
                'rsi>=70': bool((row.get('rsi') or 0) >= 70),
                'macd_histogram>0': bool((row.get('macd_histogram') or 0) > 0),
                'price>vwap': bool(row.get('price_above_vwap')),
            },
            'filters': {
                'risk': row.get('risk'),
                'volume_ratio': row.get('volume_ratio'),
                'lower_band_touch': row.get('lower_band_touch'),
                'upper_band_touch': row.get('upper_band_touch'),
            },
        }

    def _mexc_confidence(self, row: dict, signal: str):
        if signal == 'BUY':
            return max(0, min(100, (float(row.get('setup_score') or 0) / 6) * 100))
        if signal == 'SELL':
            return max(0, min(100, (float(row.get('sell_score') or 0) / 5) * 100))
        return 0

    def _confidence_percent(self, value):
        number = self._float(value) or 0
        return round(number * 100, 2) if 0 < number <= 1 else round(number, 2)

    def _polymarket_trade(self, signal: dict, market: dict, threshold: float):
        confidence = signal.get('confidence')
        side = signal.get('preferred_side')
        if not signal.get('meets_threshold') or not isinstance(confidence, int | float) or confidence < threshold:
            return {'side': 'NONE', 'reason': 'probabilidad debajo del umbral'}
        if side not in {'Up', 'Down'}:
            return {'side': 'NONE', 'reason': 'sin lado preferido'}
        if (market.get('seconds_to_close') or 0) < 45:
            return {'side': 'NONE', 'reason': 'cierre demasiado cercano'}
        ask = self._ask_for_side(market, side)
        probability = self._side_probability(signal, side)
        if ask is None or probability is None:
            return {'side': 'NONE', 'reason': 'falta order book o probabilidad'}
        full_kelly = (probability - ask) / (1 - ask) if ask < 1 else 0
        if full_kelly <= 0:
            return {'side': 'NONE', 'reason': 'Kelly<=0'}
        return {
            'side': 'UP' if side == 'Up' else 'DOWN',
            'ask': ask,
            'token_id': self._token_id_for_side(market, side),
            'full_kelly': full_kelly,
            'fractional_kelly': full_kelly * 0.25,
            'reason': 'probabilidad y Kelly positivos',
        }

    def _kelly_fraction(self, probability, ask):
        probability = self._float(probability)
        ask = self._float(ask)
        if probability is None or ask is None or ask <= 0 or ask >= 1:
            return 0
        return max(0, (probability - ask) / (1 - ask))

    def _token_id_for_side(self, market: dict, side: str):
        for token in market.get('tokens') or []:
            if token.get('outcome') == side:
                return token.get('token_id')
        return None

    def _ask_for_side(self, market: dict, side: str):
        for token in market.get('tokens') or []:
            if token.get('outcome') == side:
                return self._float(((token.get('book') or {}).get('best_ask') or {}).get('price'))
        return None

    def _side_probability(self, signal: dict, side: str):
        up_probability = (signal.get('prophet') or {}).get('up_probability')
        if isinstance(up_probability, int | float):
            return up_probability if side == 'Up' else 1 - up_probability
        confidence = signal.get('confidence')
        if isinstance(confidence, int | float) and signal.get('preferred_side') == side:
            return confidence
        return None

    def _append_audit(self, payload: dict) -> str:
        root = Path(settings.artifact_root) / 'paper_trading'
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{datetime.now(UTC).date().isoformat()}.jsonl"
        with path.open('a') as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + '\n')
        return str(path)

    def _status(self):
        root = Path(settings.artifact_root) / 'paper_trading'
        files = sorted(root.glob('*.jsonl')) if root.exists() else []
        last = files[-1] if files else None
        return {
            'mode': 'observe',
            'audit_dir': str(root),
            'latest_audit_file': str(last) if last else None,
            'live_execution_enabled': settings.polymarket_live_trading_enabled,
            'secret_exposed': False,
        }

    def _float(self, value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
