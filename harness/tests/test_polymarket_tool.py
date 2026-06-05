from tools.polymarket import PolymarketTool


def test_search_markets_uses_symbol_as_query(monkeypatch):
    tool = PolymarketTool()
    captured = {}

    monkeypatch.setattr(tool, "_base_urls", lambda: ("https://gamma", "https://data", "https://clob"))

    def fake_get(base, path, params=None):
        captured.update(params or {})
        return [
            {"id": "1", "question": "Will bitcoin hit 1m?", "slug": "bitcoin-1m"},
            {"id": "2", "question": "New album before GTA VI?", "slug": "album-gta"},
        ]

    monkeypatch.setattr(tool, "_get", fake_get)

    result = tool.run(action="search_markets", role="admin", symbol="bitcoin")

    assert captured["search"] == "bitcoin"
    assert result["markets"][0]["question"] == "Will bitcoin hit 1m?"


def test_crypto_updown_markets_fetches_interval_slugs_and_books(monkeypatch):
    tool = PolymarketTool()
    calls = []

    monkeypatch.setattr(tool, "_base_urls", lambda: ("https://gamma", "https://data", "https://clob"))

    def fake_get(base, path, params=None):
        calls.append((base, path, params))
        if path.startswith("/events/slug/btc-updown-15m-"):
            return {
                "slug": "btc-updown-15m-123",
                "title": "Bitcoin Up or Down - 15m",
                "startTime": "2026-05-19T22:00:00Z",
                "endDate": "2026-05-19T22:15:00Z",
                "markets": [{
                    "id": "m-15",
                    "question": "Bitcoin Up or Down - 15m",
                    "outcomes": '["Up", "Down"]',
                    "clobTokenIds": '["up-15", "down-15"]',
                    "active": True,
                    "closed": False,
                }],
            }
        if path == "/book":
            return {
                "bids": [{"price": "0.40", "size": "20"}, {"price": "0.45", "size": "10"}],
                "asks": [{"price": "0.58", "size": "5"}, {"price": "0.55", "size": "8"}],
            }
        raise AssertionError(path)

    monkeypatch.setattr(tool, "_get", fake_get)
    monkeypatch.setattr(tool, "_chainlink_price_frame", lambda epoch, now_ts, seconds: {
        "price_to_beat_reference": 100000.0,
        "price_to_beat_source": "polymarket_chainlink_15m_open",
        "start_price_reference": 100000.0,
        "current_price_reference": 100120.5,
        "current_price_source": "polymarket_chainlink_1m_close",
    })
    monkeypatch.setattr(tool, "_btc_reference_price", lambda epoch: 99999.0)
    monkeypatch.setattr(tool, "_btc_current_price", lambda: 99998.0)

    result = tool.run(
        action="crypto_updown_markets",
        role="trader",
        asset="btc",
        intervals=["15m"],
        include_order_books=True,
    )

    market = result["markets"][0]
    assert market["interval"] == "15m"
    assert market["tokens"][0]["outcome"] == "Up"
    assert market["tokens"][0]["book"]["best_bid"]["price"] == "0.45"
    assert market["tokens"][0]["book"]["best_ask"]["price"] == "0.55"
    assert market["price_to_beat_reference"] == 100000.0
    assert market["price_to_beat_source"] == "polymarket_chainlink_15m_open"
    assert market["start_price_reference"] == 100000.0
    assert market["current_price_reference"] == 100120.5
    assert market["current_price_source"] == "polymarket_chainlink_1m_close"
    assert market["side_now_reference"] == "Up"
    assert market["countdown"]
    assert market["timezone"] == "America/New_York"
    assert market["timezone_label"] == "EDT"
    assert market["start_time_et"].endswith("EDT")
    assert market["end_time_et"].endswith("EDT")
    assert market["next_reset_time_et"] == market["end_time_et"]
    assert market["reset_schedule_timezone"] == "America/New_York"
    assert calls[0][1].startswith("/events/slug/btc-updown-15m-")


def test_order_book_summary_sorts_best_bid_and_ask():
    tool = PolymarketTool()

    book = tool._book_summary({
        "bids": [{"price": "0.10", "size": "1"}, {"price": "0.35", "size": "2"}],
        "asks": [{"price": "0.90", "size": "3"}, {"price": "0.70", "size": "4"}],
    })

    assert book["best_bid"]["price"] == "0.35"
    assert book["best_ask"]["price"] == "0.70"


def test_scalping_signal_uses_prophet_probability(monkeypatch):
    tool = PolymarketTool()

    monkeypatch.setattr(tool, "_base_urls", lambda: ("https://gamma", "https://data", "https://clob"))
    monkeypatch.setattr(
        tool,
        "_crypto_updown_markets",
        lambda gamma, clob, **kwargs: {
            "markets": [{
                "interval": "5m",
                "question": "Bitcoin Up or Down - 5m",
                "countdown": "02:30",
                "timezone": "America/New_York",
                "start_time_et": "2026-05-19 18:10:00 EDT",
                "end_time_et": "2026-05-19 18:15:00 EDT",
                "next_reset_time_et": "2026-05-19 18:15:00 EDT",
                "price_to_beat_reference": 100.5,
                "price_to_beat_source": "polymarket_market.priceToBeat",
                "start_price_reference": 100.0,
                "current_price_reference": 101.0,
                "price_delta_reference": 0.5,
                "side_now_reference": "Up",
                "tokens": [
                    {"outcome": "Up", "book": {"best_bid": {"price": "0.82"}, "best_ask": {"price": "0.84"}}},
                    {"outcome": "Down", "book": {"best_bid": {"price": "0.16"}, "best_ask": {"price": "0.18"}}},
                ],
            }]
        },
    )
    captured = {}

    def fake_klines(interval, limit):
        captured.update({"symbol": "BTCUSDT", "interval": interval, "limit": limit})
        return [{"timestamp_ms": 1, "close": 100.0}] * 60

    monkeypatch.setattr(tool, "_chainlink_klines", fake_klines)
    monkeypatch.setattr(tool, "_mexc_klines", lambda *args, **kwargs: [])
    def fake_prophet(klines, target, end_time):
        captured["target"] = target
        return {"status": "ok", "up_probability": 0.83}

    monkeypatch.setattr(tool, "_prophet_probability", fake_prophet)

    result = tool.run(
        action="btc_updown_scalping_signal",
        role="trader",
        threshold=0.8,
        intervals=["5m"],
        candle_interval="5m",
        lookback_window="1d",
        lookback=288,
    )

    signal = result["signals"][0]
    assert {k: captured[k] for k in ["symbol", "interval", "limit"]} == {"symbol": "BTCUSDT", "interval": "1m", "limit": 90}
    assert signal["price_to_beat_reference"] == 100.5
    assert captured["target"] == 100.5
    assert signal["prediction_candle_interval"] == "1m"
    assert signal["prediction_lookback"] == 90
    assert signal["preferred_side"] == "Up"
    assert signal["confidence"] == 0.83
    assert signal["meets_threshold"] is True
    assert signal["timezone"] == "America/New_York"
    assert signal["end_time_et"].endswith("EDT")
    assert signal["lstm"]["status"] == "not_configured"


def test_price_frame_uses_price_to_beat_for_delta():
    tool = PolymarketTool()
    tool._chainlink_price_frame = lambda epoch, now_ts, seconds: {}
    tool._btc_reference_price = lambda epoch: 100.0
    tool._btc_current_price = lambda: 101.25

    frame = tool._price_frame(123, 123, None, 100.75, "polymarket_market.priceToBeat")

    assert frame["price_to_beat_reference"] == 100.75
    assert frame["price_to_beat_source"] == "polymarket_market.priceToBeat"
    assert frame["start_price_reference"] == 100.0
    assert frame["price_delta_reference"] == 0.5
    assert frame["side_now_reference"] == "Up"


def test_price_frame_prefers_chainlink_market_data():
    tool = PolymarketTool()
    tool._chainlink_price_frame = lambda epoch, now_ts, seconds: {
        "price_to_beat_reference": 100.5,
        "price_to_beat_source": "polymarket_chainlink_5m_open",
        "start_price_reference": 100.5,
        "current_price_reference": 99.25,
        "current_price_source": "polymarket_chainlink_1m_close",
    }
    tool._btc_reference_price = lambda epoch: 101.0
    tool._btc_current_price = lambda: 102.0

    frame = tool._price_frame(123, 456, 300)

    assert frame["price_to_beat_reference"] == 100.5
    assert frame["current_price_reference"] == 99.25
    assert frame["price_delta_reference"] == -1.25
    assert frame["side_now_reference"] == "Down"


def test_chainlink_price_frame_uses_market_open_and_latest_close():
    tool = PolymarketTool()

    def fake_candles(interval, limit=60, end_time_ms=None):
        if interval == "5m":
            return [
                {"time": 100, "open": 10.0, "close": 11.0},
                {"time": 300, "open": 12.5, "close": 12.0},
            ]
        if interval == "1m":
            return [{"time": 350, "close": 11.75}]
        return []

    tool._chainlink_candles = fake_candles

    frame = tool._chainlink_price_frame(300, 360, 300)

    assert frame["price_to_beat_reference"] == 12.5
    assert frame["current_price_reference"] == 11.75


def test_coordinated_signal_requires_both_windows_and_microstructure(monkeypatch):
    tool = PolymarketTool()

    monkeypatch.setattr(tool, "_base_urls", lambda: ("https://gamma", "https://data", "https://clob"))
    monkeypatch.setattr(
        tool,
        "_btc_updown_scalping_signal",
        lambda gamma, clob, **kwargs: {
            "signals": [
                {
                    "interval": "5m",
                    "preferred_side": "Up",
                    "confidence": 0.86,
                    "meets_threshold": True,
                    "prophet": {"up_probability": 0.86},
                    "countdown": "03:00",
                    "start_time_et": "2026-05-19 18:10:00 EDT",
                    "end_time_et": "2026-05-19 18:15:00 EDT",
                },
                {
                    "interval": "15m",
                    "preferred_side": "Up",
                    "confidence": 0.84,
                    "meets_threshold": True,
                    "prophet": {"up_probability": 0.84},
                    "countdown": "12:00",
                    "start_time_et": "2026-05-19 18:00:00 EDT",
                    "end_time_et": "2026-05-19 18:15:00 EDT",
                },
            ],
            "markets": [
                {
                    "interval": "5m",
                    "seconds_to_close": 180,
                    "tokens": [
                        {"outcome": "Up", "book": {"best_bid": {"price": "0.76", "size": "10"}, "best_ask": {"price": "0.80", "size": "5"}}},
                        {"outcome": "Down", "book": {"best_bid": {"price": "0.18", "size": "10"}, "best_ask": {"price": "0.22", "size": "5"}}},
                    ],
                },
                {
                    "interval": "15m",
                    "seconds_to_close": 720,
                    "tokens": [
                        {"outcome": "Up", "book": {"best_bid": {"price": "0.75", "size": "10"}, "best_ask": {"price": "0.79", "size": "5"}}},
                        {"outcome": "Down", "book": {"best_bid": {"price": "0.19", "size": "10"}, "best_ask": {"price": "0.23", "size": "5"}}},
                    ],
                },
            ],
        },
    )

    result = tool.run(
        action="btc_updown_5m15m_coordinated_signal",
        role="trader",
        threshold=0.8,
        min_edge=0.03,
        max_spread=0.08,
        min_ask_size=1,
    )

    assert result["action"] == "TRADE"
    assert result["side"] == "UP"
    assert [c["passes_filters"] for c in result["candidates"]] == [True, True]
    assert result["candidates"][0]["edge"] == 0.06


def test_coordinated_signal_uses_hybrid_probability_for_edge(monkeypatch):
    tool = PolymarketTool()

    monkeypatch.setattr(tool, "_base_urls", lambda: ("https://gamma", "https://data", "https://clob"))
    monkeypatch.setattr(
        tool,
        "_btc_updown_scalping_signal",
        lambda gamma, clob, **kwargs: {
            "signals": [
                {
                    "interval": "5m",
                    "preferred_side": "Up",
                    "confidence": 0.90,
                    "hybrid_probability_up": 0.90,
                    "prophet": {"up_probability": 0.55},
                    "countdown": "03:00",
                    "start_time_et": "2026-05-19 18:10:00 EDT",
                    "end_time_et": "2026-05-19 18:15:00 EDT",
                },
                {
                    "interval": "15m",
                    "preferred_side": "Down",
                    "confidence": 0.90,
                    "hybrid_probability_up": 0.10,
                    "prophet": {"up_probability": 0.45},
                    "countdown": "12:00",
                    "start_time_et": "2026-05-19 18:00:00 EDT",
                    "end_time_et": "2026-05-19 18:15:00 EDT",
                },
            ],
            "markets": [
                {"interval": "5m", "seconds_to_close": 180, "tokens": [{"outcome": "Up", "book": {"best_bid": {"price": "0.50", "size": "10"}, "best_ask": {"price": "0.56", "size": "5"}}}]},
                {"interval": "15m", "seconds_to_close": 720, "tokens": [{"outcome": "Down", "book": {"best_bid": {"price": "0.50", "size": "10"}, "best_ask": {"price": "0.56", "size": "5"}}}]},
            ],
        },
    )

    result = tool.run(action="btc_updown_5m15m_coordinated_signal", role="trader", threshold=0.8)

    assert result["candidates"][0]["probability"] == 0.90
    assert result["candidates"][0]["edge"] == 0.34
    assert result["candidates"][1]["probability"] == 0.90
    assert result["candidates"][1]["edge"] == 0.34


def test_adaptive_profile_blocks_expensive_5m_and_allows_15m(monkeypatch):
    tool = PolymarketTool()

    monkeypatch.setattr(tool, "_base_urls", lambda: ("https://gamma", "https://data", "https://clob"))
    monkeypatch.setattr(
        tool,
        "_btc_updown_scalping_signal",
        lambda gamma, clob, **kwargs: {
            "signals": [
                {"interval": "5m", "preferred_side": "Up", "confidence": 0.86, "hybrid_probability_up": 0.86},
                {"interval": "15m", "preferred_side": "Up", "confidence": 0.82, "hybrid_probability_up": 0.82},
            ],
            "markets": [
                {"interval": "5m", "seconds_to_close": 180, "tokens": [{"outcome": "Up", "book": {"best_bid": {"price": "0.68", "size": "10"}, "best_ask": {"price": "0.70", "size": "5"}}}]},
                {"interval": "15m", "seconds_to_close": 720, "tokens": [{"outcome": "Up", "book": {"best_bid": {"price": "0.58", "size": "10"}, "best_ask": {"price": "0.60", "size": "5"}}}]},
            ],
        },
    )

    result = tool.run(action="btc_updown_5m15m_coordinated_signal", role="trader", strategy_profile="adaptive_5m15m")

    assert result["strategy_profile"] == "adaptive_5m15m"
    assert result["action"] == "TRADE"
    assert result["side"] == "UP"
    assert result["candidates"][0]["passes_filters"] is False
    assert "ask_too_expensive" in result["candidates"][0]["reasons"]
    assert result["candidates"][1]["passes_filters"] is True


def test_independent_signal_allows_direction_conflict(monkeypatch):
    tool = PolymarketTool()

    monkeypatch.setattr(tool, "_base_urls", lambda: ("https://gamma", "https://data", "https://clob"))
    monkeypatch.setattr(
        tool,
        "_btc_updown_scalping_signal",
        lambda gamma, clob, **kwargs: {
            "signals": [
                {"interval": "5m", "preferred_side": "Up", "confidence": 0.86, "meets_threshold": True, "prophet": {"up_probability": 0.86}},
                {"interval": "15m", "preferred_side": "Down", "confidence": 0.84, "meets_threshold": True, "prophet": {"up_probability": 0.16}},
            ],
            "markets": [
                {"interval": "5m", "seconds_to_close": 180, "tokens": [{"outcome": "Up", "book": {"best_bid": {"price": "0.76", "size": "10"}, "best_ask": {"price": "0.80", "size": "5"}}}]},
                {"interval": "15m", "seconds_to_close": 720, "tokens": [{"outcome": "Down", "book": {"best_bid": {"price": "0.75", "size": "10"}, "best_ask": {"price": "0.79", "size": "5"}}}]},
            ],
        },
    )

    result = tool.run(action="btc_updown_5m15m_coordinated_signal", role="trader")

    assert result["action"] == "TRADE"
    assert result["side"] == "MIXED"
    assert result["reasons"] == []
    assert [c["passes_filters"] for c in result["candidates"]] == [True, True]


def test_short_window_prediction_is_anchored_to_latest_price():
    tool = PolymarketTool()
    base = 1_779_572_100_000
    klines = [
        {"timestamp_ms": base + i * 60_000, "close": 100 + i * 0.2}
        for i in range(30)
    ]
    result = tool._prophet_probability(klines, 104, "2026-05-23T21:40:00Z")

    assert result["model"] == "chainlink_1m_bounded_nowcast"
    assert abs(result["forecast_price_at_close"] - klines[-1]["close"]) < 10
    assert 0 <= result["up_probability"] <= 1


def test_technical_probability_targets_price_to_beat():
    tool = PolymarketTool()
    base = 1_779_572_100_000
    klines = [
        {
            "timestamp_ms": base + i * 60_000,
            "open": 100 + i * 0.11,
            "high": 100 + i * 0.11 + 0.18,
            "low": 100 + i * 0.11 - 0.12,
            "close": 100 + i * 0.11,
        }
        for i in range(90)
    ]

    result = tool._technical_probability(klines, 107.0, "2026-05-23T21:40:00Z", model_path="/missing/model.joblib")

    assert result["model"] == "chainlink_technical_score"
    assert result["status"] == "ok_rules_only"
    assert 0 <= result["up_probability"] <= 1
    assert result["features"]["target_price"] == 107.0
    assert result["trained_model_status"] == "missing_model"


def test_hybrid_probability_falls_back_to_nowcast_when_technical_missing():
    tool = PolymarketTool()

    result = tool._hybrid_probability(
        {"model": "chainlink_1m_bounded_nowcast", "up_probability": 0.83, "forecast_price_at_close": 101.0},
        {"status": "insufficient_data", "up_probability": None},
    )

    assert result["model"] == "chainlink_1m_bounded_nowcast"
    assert result["up_probability"] == 0.83
    assert result["components"]["technical_weight"] == 0.0
