#!/usr/bin/env python3
"""전방(배포경로) 라이브 감사 — (A) 확률 스케일 도달가능성 (B) 라이브 돈 게이트 검정력.

왜 이 도구가 필요한가 (2026-10-10 엔지니어 자율):
  - CG75/CG137 의 전방 판정은 'n_dates >= 10' 과 'Δ>=+0.1%p & t>=2' 를 쓴다. 그런데
    전방 money 의 **검정력**은 한 번도 재지 않았다(CG111 은 백테스트 돈 축 MDE 만 쟀다).
    문턱이 도달 불가능하면 '미검출'과 '신호없음'을 영원히 구분하지 못한다 — 그걸 미리 계산한다.
  - 소비자(트레이더)는 절대문턱(0.55)으로 진입한다. 모델이 내보내는 confidence 가 그 문턱에
    닿을 수 있는지(도달가능성)를 날짜별로 재면 '무진입'이 정책 문제인지 모델 문제인지 갈린다.
  - 예측 테이블의 '퇴화 날짜'(confidence 가 전 종목 동일 = AUC 정확히 0.5)를 세어 전방 AUC
    평균이 희석되는지 확인한다.

읽기 전용. 판정·기준선은 건드리지 않는다(측정 정합성 전용).
사용: docker exec stock_xgboost_ml sh -c 'cd /app && python -u scripts/forward_live_audit.py'
"""
from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict


def _connect():
    import psycopg2
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )


def _num(x):
    try:
        return float(x)
    except Exception:
        return None


def confidence_reachability(threshold: float) -> dict:
    """날짜별 confidence 분포 + 절대문턱 도달가능성 + 퇴화 날짜."""
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT prediction_date, confidence FROM ml_predictions "
        "WHERE confidence IS NOT NULL"
    )
    by_date: dict[str, list] = defaultdict(list)
    for d, c in cur.fetchall():
        by_date[str(d)[:10]].append(_num(c))
    conn.close()

    rows = []
    degenerate = []
    for d in sorted(by_date):
        vals = sorted(v for v in by_date[d] if v is not None)
        if not vals:
            continue
        n = len(vals)

        def q(p):
            return vals[min(n - 1, int(p * n))]

        nd = len(set(vals))
        r = {
            "date": d,
            "n": n,
            "n_distinct": nd,
            "min": round(vals[0], 4),
            "p50": round(q(0.50), 4),
            "p90": round(q(0.90), 4),
            "max": round(vals[-1], 4),
            "frac_ge_threshold": round(sum(1 for v in vals if v >= threshold) / n, 4),
        }
        rows.append(r)
        if nd == 1:
            degenerate.append(r)

    # 퇴화 날짜가 전방 AUC 평균에 미치는 영향: 같은 날 AUC 는 정의상 정확히 0.5 다.
    auc_dates = [r for r in rows]
    n_dates = len(auc_dates)
    n_degen = len(degenerate)
    reach = {
        "threshold": threshold,
        "n_dates": n_dates,
        "n_degenerate_dates": n_degen,
        "degenerate_share": round(n_degen / n_dates, 4) if n_dates else None,
        "dates_with_any_ge_threshold": sum(1 for r in rows if r["frac_ge_threshold"] > 0),
        "max_conf_overall": max((r["max"] for r in rows), default=None),
    }
    return {"per_date": rows, "degenerate_dates": degenerate, "reachability": reach}


def forward_money_power(path: str, deltas=(0.1, 0.25, 0.5, 1.0), t_target=2.0,
                        sessions_per_year=250.0) -> dict:
    """전방 money Δ 의 날짜별 표준편차로 '문턱 검출에 필요한 세션 수'를 계산.

    근사: n >= (t_target * sd / delta)^2 (세션당 독립 가정). sd 는 scorecard 의
    date_deltas_pct 에서 직접 계산한다(자기신고 없이 파일에서).
    """
    with open(path) as f:
        sc = json.load(f)
    out = {"source": path, "generated_at": sc.get("generated_at"), "horizons": {}}
    for h, v in (sc.get("result") or {}).items():
        money = v.get("money") or {}
        ds = money.get("date_deltas_pct") or []
        n = len(ds)
        if n < 2:
            out["horizons"][h] = {
                "n_dates": n,
                "pooled_auc": v.get("pooled_auc"),
                "daily_auc_mean": v.get("daily_auc_mean"),
                "note": "세션 2개 미만 — sd 계산 불가",
            }
            continue
        mean = sum(ds) / n
        sd = (sum((x - mean) ** 2 for x in ds) / (n - 1)) ** 0.5
        se = sd / (n ** 0.5)
        req = {}
        for delta in deltas:
            need = (t_target * sd / delta) ** 2
            req[f"delta_{delta}"] = {
                "sessions_needed": round(need, 1),
                "years_at_250_per_year": round(need / sessions_per_year, 2),
            }
        out["horizons"][h] = {
            "n_dates": n,
            "pooled_auc": v.get("pooled_auc"),
            "daily_auc_mean": v.get("daily_auc_mean"),
            "delta_mean_pct": round(mean, 4),
            "delta_sd_pct": round(sd, 4),
            "delta_se_pct": round(se, 4),
            "delta_t": round(mean / se, 3) if se else None,
            "required_sessions": req,
        }
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scorecard", default="/app/reports/overnight/forward_scorecard.json")
    ap.add_argument("--threshold", type=float, default=0.55,
                    help="소비자 절대문턱(swing) — 도달가능성 판정 기준")
    ap.add_argument("--json-out", default="/app/reports/overnight/forward_live_audit.json")
    args = ap.parse_args()

    conf = confidence_reachability(args.threshold)
    power = forward_money_power(args.scorecard)
    payload = {"tool": "forward_live_audit", "confidence": conf, "money_power": power}

    os.makedirs(os.path.dirname(args.json_out), exist_ok=True)
    with open(args.json_out, "w") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    r = conf["reachability"]
    print(f"[confidence] 날짜 {r['n_dates']} · 퇴화 {r['n_degenerate_dates']}"
          f"({r['degenerate_share']}) · 문턱 {r['threshold']} 도달 날짜"
          f" {r['dates_with_any_ge_threshold']} · 전기간 최대 confidence {r['max_conf_overall']}")
    for row in conf["degenerate_dates"]:
        print(f"  [퇴화] {row['date']} n={row['n']} 단일값={row['min']}")
    for h, v in power["horizons"].items():
        if "delta_sd_pct" not in v:
            print(f"[money {h}] {v.get('note')}")
            continue
        need = v["required_sessions"]["delta_0.1"]["sessions_needed"]
        print(f"[money {h}] n_dates {v['n_dates']} Δ{v['delta_mean_pct']}%p"
              f" sd {v['delta_sd_pct']} t {v['delta_t']}"
              f" → 문턱 +0.1%p 검출에 세션 {need} 개"
              f"({v['required_sessions']['delta_0.1']['years_at_250_per_year']}년)")
    print(f"[ok] {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
