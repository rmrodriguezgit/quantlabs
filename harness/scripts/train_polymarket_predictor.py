from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.metrics import accuracy_score, log_loss
from sklearn.preprocessing import StandardScaler

from tools.polymarket import PolymarketTool, TECHNICAL_MODEL_PATH


def main() -> None:
    tool = PolymarketTool()
    limit = 5000
    sequence_length = 90
    candles = tool._chainlink_klines_extended("1m", limit)
    samples = tool._btc_updown_5m_samples(candles, sequence_length=sequence_length)
    rows = []
    for sample in samples:
        end_time = datetime.fromtimestamp(sample["end_ts"], UTC).isoformat().replace("+00:00", "Z")
        history = [row for row in candles if int(row["timestamp_ms"] // 1000) < int(sample["start_ts"])]
        history = history[-sequence_length:]
        features = tool._technical_feature_frame(history, sample["price_to_beat_reference"], end_time)
        if features.get("status") != "ok":
            continue
        rows.append({
            "window_et": sample["window_et"],
            "label": int(sample["label"]),
            **{name: float(features.get(name) or 0.0) for name in tool._technical_feature_names()},
        })

    if len(rows) < 80:
        raise SystemExit(f"insufficient_samples: {len(rows)}")

    feature_names = tool._technical_feature_names()
    X = np.array([[row[name] for name in feature_names] for row in rows], dtype=float)
    y = np.array([row["label"] for row in rows], dtype=int)
    split = int(len(rows) * 0.80)
    X_train, X_test = X[:split], X[split:]
    y_train, y_test = y[:split], y[split:]

    scaler = StandardScaler()
    X_train_s = scaler.fit_transform(X_train)
    X_test_s = scaler.transform(X_test)
    model = GradientBoostingClassifier(
        n_estimators=160,
        learning_rate=0.035,
        max_depth=2,
        subsample=0.85,
        random_state=42,
    )
    model.fit(X_train_s, y_train)
    pred = model.predict(X_test_s)
    proba = model.predict_proba(X_test_s)

    metrics = {
        "status": "ok",
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "samples": len(rows),
        "train_samples": int(len(y_train)),
        "test_samples": int(len(y_test)),
        "accuracy": round(float(accuracy_score(y_test, pred)), 4),
        "log_loss": round(float(log_loss(y_test, proba, labels=model.classes_)), 4) if len(set(y_test.tolist())) > 1 else None,
        "up_rate": round(float(np.mean(y)), 4),
        "feature_names": feature_names,
        "target": "Polymarket 5m close above fixed price_to_beat",
        "data_source": "Polymarket Chainlink 1m candles",
    }

    TECHNICAL_MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({
        "kind": "polymarket_btc_updown_technical",
        "model": model,
        "scaler": scaler,
        "feature_names": feature_names,
        "metrics": metrics,
    }, TECHNICAL_MODEL_PATH)
    metadata_path = TECHNICAL_MODEL_PATH.with_suffix(".metadata.json")
    metadata_path.write_text(json.dumps(metrics, indent=2, ensure_ascii=False))
    print(json.dumps({
        "model_path": str(TECHNICAL_MODEL_PATH),
        "metadata_path": str(metadata_path),
        "metrics": metrics,
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
