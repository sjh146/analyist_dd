"""CG156 구현 — '무개선 카운터'가 잡음바닥 안에서 세어졌는지 감사한다(CG206).

배경(실측 근거):
  - 스코어보드는 등록 기준선 0.5406 대비 +0.02 미만이면 '무개선'으로 센다(19사이클 연속).
  - CG203 실측(2026-11-12, 널 쌍 5앵커·1~60거래일 간격): 짝 Δ 잡음 std 0.0014~0.0026.
  - CG201 실측: 근접앵커(0~2거래일) 짝 Δ std 0.0013.
  따라서 잡음바닥 ±0.003 과 사전문턱 0.02 는 6.7배 떨어져 있다 — 카운터가 잡음 위에서
  세어졌는지는 '각 사이클의 Δ 가 잡음바닥 밖인가'로 판정할 수 있다.

이 스크립트는 원장의 사이클별 최고 비교가능 arm 평균을 뽑아 Δ 를 잡음바닥과 비교한다.
읽기 전용 — 스코어보드 기준선·프로토콜을 바꾸지 않는다(변경은 리뷰보드 승인 대상).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

import quant_scoreboard as qs  # noqa: E402  (스코어보드의 비교가능성 로직을 그대로 재사용)

DEFAULT_FLOOR = 0.003  # CG201·CG203 실측 상한(짝 Δ std 0.0013~0.0026)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", default=str(ROOT / "data/reports/model_engineer_ledger.jsonl"))
    ap.add_argument("--baseline", type=float, default=qs.BASELINE_ROBUST)
    ap.add_argument("--threshold", type=float, default=0.02)
    ap.add_argument("--floor", type=float, default=DEFAULT_FLOOR)
    ap.add_argument("--json-out", default=str(ROOT / "data/reports/noise_floor_audit.json"))
    args = ap.parse_args()

    rows = qs._jsonl(args.ledger)
    cycles = []
    for rec in rows:
        mean = qs._rec_best_mean(rec)
        if mean is None:
            continue
        delta = mean - args.baseline
        if abs(delta) <= args.floor:
            cls = "noise_band"          # 잡음 안 — 개선/악화 판정 불가
        elif delta >= args.threshold:
            cls = "signal"              # 사전문턱 통과
        elif delta > 0:
            cls = "below_threshold"     # 잡음 밖이지만 문턱 미달 = 실재하는 미미한 효과
        else:
            cls = "worse_than_noise"    # 기준선보다 유의하게 나쁨
        cycles.append({
            "id": rec.get("id"), "ts": rec.get("ts"), "metric": rec.get("metric"),
            "best_mean": round(mean, 6), "delta": round(delta, 6), "class": cls,
            "verdict": (rec.get("verdict") or "")[:120],
        })

    # 시간순 정렬 후 '측정된 사이클'의 연속 무개선 꼬리를 센다(스코어보드와 같은 정의)
    cycles.sort(key=lambda c: c["ts"] or "")
    streak = 0
    for c in reversed(cycles):
        if c["class"] == "signal":
            break
        streak += 1
    counts = {}
    for c in cycles:
        counts[c["class"]] = counts.get(c["class"], 0) + 1

    out = {
        "audit": "CG206 — 무개선 카운터 잡음바닥 감사(CG156 구현)",
        "baseline": args.baseline,
        "pre_registered_threshold": args.threshold,
        "noise_floor": args.floor,
        "floor_basis": "CG201 짝 Δ std 0.0013 · CG203 앵커간격 1~60거래일 짝 Δ std 0.0014(level std 0.0026)",
        "measured_cycles": len(cycles),
        "counts": counts,
        "no_improve_streak": streak,
        "max_delta": max((c["delta"] for c in cycles), default=None),
        "cycles": cycles,
        "verdict": None,
    }
    if cycles:
        best = max(cycles, key=lambda c: c["delta"])
        if best["delta"] < args.threshold and best["delta"] > args.floor:
            out["verdict"] = (
                f"카운터는 잡음 위에서 세어졌다 — 최고 Δ {best['delta']:+.4f}({best['id']})가 "
                f"잡음바닥 ±{args.floor:.3f} 밖이지만 사전문턱 {args.threshold:+.2f} 미달 = "
                f"'실재하지만 미미한 효과'. 무개선 {streak}사이클은 측정 불가가 아니라 "
                f"문턱 미달의 누적이다(잔차를 잡음으로 오독하지 말 것)."
            )
        elif best["delta"] >= args.threshold:
            out["verdict"] = f"신호가 존재한다 — 최고 Δ {best['delta']:+.4f}({best['id']})가 사전문턱을 넘었다."
        else:
            out["verdict"] = (
                f"최고 Δ {best['delta']:+.4f} 가 잡음바닥 ±{args.floor:.3f} 안 = 무개선 카운터는 "
                f"측정 불가 구간에서 세어졌다(스코어보드 기준선 재검토 필요 — 리뷰보드 대상)."
            )

    Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.json_out).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"사이클 {len(cycles)}개 · 분류 {counts} · 연속 무개선 {streak}")
    print("최고 Δ 상위 6:")
    for c in sorted(cycles, key=lambda c: -c["delta"])[:6]:
        print(f"  {c['ts'][:16] if c['ts'] else '?'} {c['id']:7s} Δ{c['delta']:+.4f} {c['class']}")
    print("판정:", out["verdict"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
