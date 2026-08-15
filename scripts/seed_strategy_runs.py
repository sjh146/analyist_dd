"""strategy_runs 테이블 시드 — 기존 실제 실행 결과 (JSON 파일)에서 이력 적재."""
import json
import sys
import os
from datetime import datetime

sys.path.insert(0, "/home/dduckbeagy/analyist_dd/scripts")
from record_strategy_run import record_run


def seed():
    base = "/home/dduckbeagy/analyist_dd"
    # 1) 백테스트 (오늘 실행 — AUC 0.7129)
    try:
        d = json.load(open(f"{base}/reports/backtest_result.json"))
        record_run(
            tool="backtest",
            stocks=d.get("n_stocks", 50),
            auc=d.get("auc"),
            accuracy=d.get("acc"),
            metric_value=d.get("auc"),
            meta={"n_rows": d.get("n_rows"), "n_features": d.get("n_features"),
                  "universe": "kospi30+kosdaq20 seed42"},
        )
        print("backtest 시드 OK")
    except Exception as e:
        print("backtest 시드 실패:", e)

    # 2) 스윙스크리너 (오늘 08:35 실행 — 1765종목)
    try:
        d = json.load(open(f"{base}/reports/swing_latest.json"))
        rows = d.get("candidates") or d.get("results") or []
        top = rows[:20] if rows else []
        record_run(
            tool="swing_screener",
            stocks=d.get("total", len(rows)),
            errors=d.get("errors", 0),
            metric_value=top[0].get("expected_return") if top else None,
            meta={"top20": [{"code": c.get("stock_code"), "conf": c.get("confidence"),
                             "exp_ret": c.get("expected_return")} for c in top],
                  "auc": d.get("auc")},
        )
        print("swing_screener 시드 OK")
    except Exception as e:
        print("swing_screener 시드 실패:", e)

    # 3) 강환국 팩터 (2026-08-13 실행)
    try:
        d = json.load(open(f"{base}/services/strategy-agents/reports/factor_strategies_result.json"))
        for name, st in d.get("strategies", {}).items():
            m = st.get("metrics", {})
            record_run(
                tool=f"factor_{name}",
                metric_value=round(float(m.get("total_return", 0) or 0), 5),
                meta={"sharpe": m.get("sharpe"), "max_drawdown": m.get("max_drawdown"),
                      "win_rate": m.get("win_rate"), "num_trades": m.get("num_trades")},
            )
        print("factor 시드 OK")
    except Exception as e:
        print("factor 시드 실패:", e)

    # 4) 모델 재학습 (2026-08-14 11:02 — 챔피언 173피처)
    try:
        d = json.load(open(f"{base}/services/xgboost-ml/app/models/champion/training-result-20260814-110235.json"))
        record_run(
            tool="model_retrain",
            stocks=None,
            auc=float(d.get("ensemble_auc", 0) or 0),
            metric_value=float(d.get("ensemble_auc", 0) or 0),
            meta={"model_aucs": d.get("model_aucs", {}), "n_rows": d.get("n_rows"),
                  "n_features": d.get("n_features"), "up_rate": d.get("up_rate"),
                  "seed": d.get("seed")},
        )
        print("model_retrain 시드 OK")
    except Exception as e:
        print("model_retrain 시드 실패:", e)


if __name__ == "__main__":
    seed()
