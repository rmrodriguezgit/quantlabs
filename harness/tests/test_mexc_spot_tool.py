from tools.mexc_spot import MexcSpotTool


def test_mexc_scan_adds_buy_sell_none_signal_and_extra_criteria(monkeypatch):
    tool = MexcSpotTool()

    klines = []
    price = 100.0
    for idx in range(80):
        close = price - (idx * 0.2)
        high = close * 1.01
        low = close * 0.99
        volume = 1000 + idx
        quote_volume = close * volume
        klines.append([idx, close, high, low, close, volume, idx + 1, quote_volume])

    monkeypatch.setattr(tool, "_public", lambda path, params=None: klines)

    result = tool.run(
        action="scan_spot_long_candidates",
        role="trader",
        tickers=["TEST/USDT"],
        interval="15m",
        limit=80,
    )

    row = result["results"][0]
    assert row["symbol"] == "TESTUSDT"
    assert row["signal"] in {"BUY", "SELL", "NONE"}
    assert "ema20" in row
    assert "ema50" in row
    assert "bb_lower" in row
    assert "atr_pct" in row
    assert "volume_ratio" in row
    assert "setup_score" in row
    assert "sell_score" in row
    assert "price_above_vwap" in row
    assert "rsi_overbought" in row
    assert "macd_positive" in row
    assert "upper_band_touch" in row
    assert "risk" in row
