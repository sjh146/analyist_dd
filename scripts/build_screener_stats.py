#!/usr/bin/env python3
"""스크리너별 실측 승률/평균손익 → `data/reports/screener_stats.json` (켈리 사전확률 소스).

WHY
trader-agent 의 켈리 게이트는 ``f* = p - (1-p)/b`` (``b = avg_win/avg_loss``)로 사이징한다.
FEED_CONTRACT §6 의 우선순위는 **피드 통계 > 자체 저널(자기수정) > 설정 기본값** 인데,
2026-09-25 실측에서 피드에 ``scoring_summary`` 가 없었고 자체 저널 사전확률도
``strategy_params.json`` 이 ``reviewed_trades: 2``(최소 5건 미달)라 미기록 상태였다.
⇒ 사이징이 "측정"이 아니라 config 가정값(b=1, p=0.53)으로 돌고 있었다.

소스 (측정값만 쓴다 — 추정치를 넣지 않는다)
1) ``strategy_runs`` 의 ``tool='backtest_pnl'`` 워크포워드 기록: 구간별
   ``meta.win_rate`` / ``meta.avg_win_pct`` / ``meta.avg_loss_pct`` / ``n_trades``.
   ``avg_win_pct`` 가 없는 옛 기록은 **쓸 수 없다**(b 를 알 수 없으므로) → 건너뛴다.
2) (예정) trader-agent 저널의 청산 거래(screener 태그) — 전략당 5건 이상일 때.

출력 형상 (FEED_CONTRACT §6)
    {"screener_stats": {"close": {"win_rate": 53.3, "avg_win_pct": 2.4, "avg_loss_pct": 1.3}},
     "source": "strategy_runs/backtest_pnl", "generated_at": ...,
     "shrinkage": {...}}
``win_rate`` 는 퍼센트(엔진이 >1 이면 /100 으로 정규화), 손익도 퍼센트.

보수적 축소 (계약이 요구: "과신하지 않도록 축소 추정된 값을 보내라")
- ``p' = 0.5 + (p - 0.5) * 0.5`` , ``b' = 1 + (b - 1) * 0.5`` , 클램프 p∈[0.45,0.62], b∈[0.8,2.0]
- 측정 b<1(손실이 더 큼)이면 ``f* <= 0`` 이 되어 **모든 진입이 조용히 막힌다**. 그래서 b' 하한을
  0.8 로 두되, f*<=0 이 되는 조합은 아예 쓰지 않고 경고한다(사이징이 0이 되는 것보다
  가정값으로 남기는 편이 낫다 — 침묵 실패 방지).

사용:
    /usr/bin/python3 scripts/build_screener_stats.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

import psycopg2

PROJ = os.environ.get("PROJ_DIR", "/home/jhshi/analyist_dd")
OUT_PATHS = [
    os.path.join(PROJ, "data", "reports", "screener_stats.json"),
    os.path.join(PROJ, "reports", "screener_stats.json"),
]
KST_OFFSET = "+09:00"


def _pg():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.environ.get("POSTGRES_PORT", "5434")),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )


def shrink(p: float, b: float):
    """보수적 축소. 반환 (p', b', f*) 또는 (None, None, None) if f* <= 0."""
    ps = 0.5 + (p - 0.5) * 0.5
    bs = 1.0 + (b - 1.0) * 0.5
    ps = min(max(ps, 0.45), 0.62)
    bs = min(max(bs, 0.8), 2.0)
    f = ps - (1.0 - ps) / bs
    if f <= 0:
        return None, None, f
    return ps, bs, f


def collect_from_runs(cur):
    """tool='backtest_pnl' 워크포워드 기록에서 전략별 통계를 모은다(n 가중).

    **최신 실행 배치만** 쓴다: 챔피언이 교체되면(예: 2026-09-25 승격) 과거 배치의 승률은
    다른 모델의 성과이므로 섞으면 안 된다. 배치 = 최신 기록 시각 기준 10분 이내 행.
    """
    cur.execute("""
        SELECT meta, stocks, run_at
        FROM strategy_runs
        WHERE tool = 'backtest_pnl' AND meta IS NOT NULL
        ORDER BY run_at DESC
        LIMIT 50
    """)
    rows = cur.fetchall()
    if not rows:
        return {}
    # 앵커는 **필요한 키를 가진 행**에서 고른다 — avg 키가 없는 행(시나리오/단일 백테스트)이
    # 최신이라고 그것을 앵커로 잡으면, 워크포워드 행들이 10분 밖으로 밀려나 배치 전체가
    # 탈락한다(per={} → exit 3). 2026-09-25 위임 리뷰 지적.
    valid = [r for r in rows
             if isinstance(r[0], dict) and r[0].get("avg_win_pct") and r[0].get("avg_loss_pct")
             and (r[0].get("n_trades") or 0)]
    if not valid:
        return {}
    newest = valid[0][2]
    per = {}
    for meta, stocks, run_at in valid:
        if newest is not None and run_at is not None:
            if (newest - run_at).total_seconds() > 600:
                continue  # 다른 실행 배치(다른 모델) — 제외
        if not isinstance(meta, dict):
            continue
        wr = meta.get("win_rate")
        aw = meta.get("avg_win_pct")
        al = meta.get("avg_loss_pct")
        n = meta.get("n_trades") or 0
        if wr is None or not aw or not al or not n:
            continue  # b 를 알 수 없는 옛 기록 — 쓸 수 없다
        agg = per.setdefault("close", {"w_sum": 0.0, "aw_sum": 0.0, "al_sum": 0.0,
                                       "n": 0, "windows": 0, "last": None})
        agg["w_sum"] += float(wr) * n
        agg["aw_sum"] += float(aw) * n
        agg["al_sum"] += float(al) * n
        agg["n"] += int(n)
        agg["windows"] += 1
        agg["last"] = str(run_at)
    return per


def main() -> int:
    ap = argparse.ArgumentParser(description="켈리 사전확률용 스크리너 실측 통계 생성")
    ap.add_argument("--dry-run", action="store_true", help="쓰지 않고 결과만 출력")
    args = ap.parse_args()

    conn = _pg()
    try:
        cur = conn.cursor()
        per = collect_from_runs(cur)
        cur.close()
    finally:
        conn.close()

    if not per:
        print("[screener_stats] 측정 가능한 백테스트 기록 없음 "
              "(strategy_runs 에 avg_win_pct/avg_loss_pct 를 가진 tool='backtest_pnl' 행이 "
              "없다) → 켈리 사전확률 파일을 쓰지 않는다. 추정값을 만들지 않는다.")
        return 3

    stats, notes = {}, {}
    for screener, a in per.items():
        n = a["n"]
        p = a["w_sum"] / n
        avg_win_meas = a["aw_sum"] / n
        avg_loss_meas = a["al_sum"] / n
        b = avg_win_meas / avg_loss_meas if avg_loss_meas else 0.0
        ps, bs, f = shrink(p, b)
        if ps is None:
            notes[screener] = (f"측정 b={b:.3f} (p={p:.3f}) → 축소 후 f*={f:.4f} <= 0 "
                               f"이므로 기록하지 않음(진입 전면 차단 방지)")
            continue
        stats[screener] = {
            "win_rate": round(ps * 100.0, 2),
            # ⚠ 페이로드의 b = avg_win_pct / avg_loss_pct 여야 한다. 측정 avg_win 에 bs 를
            # 곱하면 b·bs 가 되어 **b>1 에서 오히려 증폭**된다(b=1.5, bs=1.25 → 1.875).
            # 2026-09-25 위임 리뷰(Claude Code)가 잡은 버그 — 손익비를 축소하려면
            # avg_loss 를 고정하고 avg_win = avg_loss × b' 로 만든다.
            "avg_win_pct": round(avg_loss_meas * bs, 3),
            "avg_loss_pct": round(avg_loss_meas, 3),
        }
        notes[screener] = (f"측정 p={p:.3f} b={b:.3f} (aw={avg_win_meas:.2f}%, al={avg_loss_meas:.2f}%) "
                           f"n={n} windows={a['windows']} → 축소 p'={ps:.3f} b'={bs:.3f} "
                           f"f*={f:.4f} / 페이로드 b={stats[screener]['avg_win_pct'] / stats[screener]['avg_loss_pct']:.3f}")

    if not stats:
        print("[screener_stats] 축소 후 사용 가능한 값 없음 → 켈리 파일 미기록(진입 전면 차단 방지)")
        for k, v in notes.items():
            print("  -", k, v)
        # 실측 근거는 남긴다. "측정했더니 기대값이 음수라 쓰지 않았다"는 사실 자체가
        # 사용자·리뷰보드가 봐야 할 정보다. 단 **트레이더가 읽는 경로(OUT_PATHS)에는 쓰지 않는다** —
        # 거기에 쓰면 켈리 사전확률이 0/음수로 전달되어 신규 진입이 전면 차단된다.
        if not args.dry_run:
            ev_path = OUT_PATHS[0].replace("screener_stats.json", "screener_stats_measured.json")
            payload_ev = {
                "usable": False,
                "reason": "모든 스크리너의 축소 후 기대값 f* <= 0 (측정 엣지가 음수)",
                "measured_notes": notes,
                "measured": per,
                "effect": "트레이더 페이로드 미기록 — 기존 사이징 로직이 그대로 유지된다",
                "generated_at": datetime.now().isoformat(timespec="seconds") + KST_OFFSET,
            }
            try:
                os.makedirs(os.path.dirname(ev_path), exist_ok=True)
                with open(ev_path, "w", encoding="utf-8") as f:
                    json.dump(payload_ev, f, ensure_ascii=False, indent=2)
                print(f"[screener_stats] 측정 근거 기록(트레이더 미반영): {ev_path}")
            except OSError as exc:
                print(f"[screener_stats] 근거 기록 실패: {exc}")
        return 3

    payload = {
        "screener_stats": stats,
        "source": "strategy_runs/backtest_pnl (워크포워드 구간 n-가중 평균, 보수적 축소)",
        "generated_at": datetime.now().isoformat(timespec="seconds") + KST_OFFSET,
        "notes": notes,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if args.dry_run:
        print("[screener_stats] --dry-run — 기록하지 않음")
        return 0
    for path in OUT_PATHS:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"[screener_stats] 기록: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
