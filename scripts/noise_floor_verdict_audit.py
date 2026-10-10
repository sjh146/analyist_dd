#!/usr/bin/env python3
"""잡음바닥 대비 판정 감사 — '무개선 19사이클' 카운터가 측정 가능한가?

WHY: 스코어보드의 무개선 카운터는 **각 사이클의 최고 로버스트 arm 평균**을 **고정 상수
BASELINE_ROBUST(0.5406)** 와 비교해 `+SIGNAL_DELTA(0.02)` 를 넘는지 센다
(scripts/quant_scoreboard.py L52-53·L290-291). 기준선은 특정 날의 단일 레벨이고, 사이클 arm 은
다른 날(다른 앵커·다른 패널 end-date)에 측정된다 → **날짜 간 레벨 비교**다.

CG143·CG144 실측: 같은 모델·같은 config 를 앵커만 1·2·5·7거래일 옮기면 로버스트 AUC 가
|Δ| 0.0175 / 0.0469 / 0.0579 / 0.0597 만큼 흔들린다(모델은 불변). 즉 날짜 간 레벨 비교의
잡음바닥은 문턱 0.02 의 **2.4~3.0배**다.

따라서 이 감사가 답하는 질문: (1) 무개선 카운터의 Δ 는 잡음바닥 안인가? (2) 같은 런 안에서
측정된 짝(pair) Δ 의 잡음은 얼마이고, 그 위에서 역사적 '신호있음' 판정들은 문턱을 넘었는가?

산출물: data/reports/noise_floor_verdict_audit_<stamp>.json / .md

사용: python3 scripts/noise_floor_verdict_audit.py  [--out-dir data/reports]
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KST = timezone(timedelta(hours=9))
OVERNIGHT = ROOT / "services/xgboost-ml/reports/overnight"


def _load_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text())
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 읽기 실패 {p}: {e}", file=sys.stderr)
        return {}


def _scoreboard_constants() -> dict:
    """quant_scoreboard.py 의 등록 상수를 **import 없이** 읽는다(모듈 부작용 회피)."""
    txt = (ROOT / "scripts/quant_scoreboard.py").read_text()
    out = {}
    for key in ("BASELINE_ROBUST", "SIGNAL_DELTA", "NO_IMPROVE_CYCLES"):
        m = re.search(rf"^{key}\s*=\s*([0-9.]+)", txt, re.M)
        out[key] = float(m.group(1)) if m else None
    return out


def _backlog_signals() -> list[dict]:
    """원장(백로그)에서 verdict 가 '신호있음/신호 확인' 인 항목의 Δ 를 모은다."""
    d = _load_json(ROOT / "docs/QUANT_MODEL_BACKLOG.json")
    items = d.get("items", d) if isinstance(d, dict) else d
    out = []
    for it in items:
        r = it.get("result")
        if not isinstance(r, dict):
            continue
        v = str(r.get("verdict", "")).strip()
        if v.startswith("신호있음") or v.startswith("신호 확인"):
            out.append({"id": it.get("id"), "delta": r.get("delta"), "verdict": v[:80]})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="data/reports")
    args = ap.parse_args()

    const = _scoreboard_constants()
    base = const["BASELINE_ROBUST"]
    thr = const["SIGNAL_DELTA"]

    nf = _load_json(OVERNIGHT / "cg143_noise_floor.json")
    curve = _load_json(OVERNIGHT / "cg144_noise_curve.json")
    pair = _load_json(OVERNIGHT / "cg146_pair_anchor.json")
    sig = _backlog_signals()

    if not curve or not pair:
        print("[fatal] 잡음바닥 산출물(cg144/cg146)을 찾을 수 없다", file=sys.stderr)
        return 2

    # --- 1) 날짜 간 레벨 비교 잡음바닥 (같은 모델, 앵커만 이동) ---
    deltas = curve.get("deltas", {})
    level_noise = {k: abs(float(v)) for k, v in deltas.items() if k != "d0"}
    max_level_noise = max(level_noise.values())

    # --- 2) 같은 런 안 짝 Δ 잡음 (두 모델 고정, 앵커만 이동) ---
    pd = pair.get("pair_delta", {})
    valid = pair.get("valid_anchors") or list(pd)
    pvals = [float(pd[a]) for a in valid if a in pd]
    pair_std = statistics.pstdev(pvals) if len(pvals) > 1 else 0.0
    pair_range = (max(pvals) - min(pvals)) if pvals else 0.0
    pair_over = [a for a in valid if a in pd and pd[a] >= thr and a != "d7"]

    # --- 3) 무개선 카운터의 Δ vs 잡음바닥 ---
    headline = None
    sb = _load_json(ROOT / "data/reports/quant_scoreboard.json")
    try:
        st = sb.get("engineer") or {}
        headline = st.get("best_robust") or st.get("robust_auc")
    except Exception:  # noqa: BLE001
        headline = None
    counter_delta = (float(headline) - base) if (headline is not None and base is not None) else None
    counter_ratio = (counter_delta / max_level_noise) if counter_delta is not None else None

    # --- 4) 역사적 '신호있음' Δ 가 잡음바닥/짝잡음을 넘었는가 ---
    rows = []
    for s in sig:
        dv = s.get("delta")
        if dv is None:
            rows.append({**s, "vs_level_noise": None, "vs_pair_2sigma": None})
            continue
        dv = float(dv)
        rows.append({
            **s,
            "vs_level_noise": "above" if dv > max_level_noise else "within",
            "vs_pair_2sigma": "above" if dv > 2 * pair_std else "within",
        })

    above_level = [r for r in rows if r.get("vs_level_noise") == "above"]
    above_pair = [r for r in rows if r.get("vs_pair_2sigma") == "above"]

    # --- 5) 검정력: 문턱 +0.02 를 2σ 로 분해하려면 앵커가 몇 개 필요한가 ---
    import math

    def _k_needed(sigma: float, delta: float = thr, z: float = 2.0) -> int:
        if sigma <= 0:
            return 1
        return max(1, math.ceil((z * sigma / delta) ** 2))

    k_pair = _k_needed(pair_std)
    k_level = _k_needed(max_level_noise)

    if counter_delta is None:
        verdict_line = (
            f"날짜 간 레벨 잡음바닥 최대 |Δ| {max_level_noise:.4f} = 등록 문턱+{thr:.2f} 의 "
            f"{max_level_noise / thr:.1f}배 → 무개선 카운터의 Δ(헤드라인 미확인)는 잡음바닥 안"
        )
    else:
        verdict_line = (
            f"날짜 간 레벨 잡음바닥 최대 |Δ| {max_level_noise:.4f} = 등록 문턱+{thr:.2f} 의 "
            f"{max_level_noise / thr:.1f}배 → 무개선 카운터의 Δ {counter_delta:+.4f} 는 "
            f"잡음바닥의 {counter_ratio:.2f}배(측정 불가 구간)"
        )

    stamp = datetime.now(KST)
    out_dir = ROOT / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    tag = stamp.strftime("%Y%m%d")
    payload = {
        "metric": "noise_floor_verdict_audit",
        "generated_at": stamp.isoformat(timespec="seconds"),
        "pre_registered": {"baseline_robust": base, "signal_delta": thr,
                           "no_improve_cycles_to_escalate": const["NO_IMPROVE_CYCLES"]},
        "cross_day_level_noise": {
            "source": "cg144_noise_curve.json (동일 champion·동일 config, 앵커만 이동)",
            "by_anchor_shift_days": {k: round(v, 4) for k, v in level_noise.items()},
            "max_abs_delta": round(max_level_noise, 4),
            "multiple_of_threshold": round(max_level_noise / thr, 2),
            "note": "앵커 1일 이동만으로도 |Δ|0.0175 = 문턱의 0.88배",
        },
        "within_run_pair_noise": {
            "source": "cg146_pair_anchor.json (cand-champ, 앵커 6개, d7 은 모집단 불일치로 무효)",
            "valid_anchors": valid,
            "pair_delta_by_anchor": {a: pd[a] for a in valid if a in pd},
            "mean": pair.get("pair_delta_mean"),
            "std": round(pair_std, 4),
            "range": round(pair_range, 4),
            "over_threshold": f"{len(pair_over)}/{len(valid)}",
            "sign_consistent": pair.get("sign_consistent"),
            "note": "부호는 일관되나 크기가 문턱을 넘나든다 — 단일앵커 판정 불가의 직접 증거",
        },
        "no_improve_counter": {
            "headline_robust_auc": headline,
            "registered_baseline": base,
            "delta": None if counter_delta is None else round(counter_delta, 4),
            "ratio_delta_over_noise_floor": None if counter_ratio is None else round(counter_ratio, 3),
            "diagnostic": bool(counter_delta is not None and counter_delta > max_level_noise),
            "note": "기준선은 특정 날·(CG131) 누수 패널의 단일 레벨 — 날짜 간 비교라 잡음바닥 안",
        },
        "historical_signals": {
            "n": len(rows),
            "above_cross_day_level_noise": len(above_level),
            "above_pair_2sigma": len(above_pair),
            "detail": rows,
        },
        "power": {
            "threshold": thr,
            "z": 2.0,
            "anchors_needed_pair_protocol": k_pair,
            "anchors_needed_cross_day_level": k_level,
            "note": "앵커 k개의 평균은 잡음을 ∝1/√k 로 줄인다 — 같은 런 짝 프로토콜은 사실상 도달 가능, "
                    "날짜 간 레벨 비교는 불가능에 가깝다",
        },
        "verdict": verdict_line,
    }
    jp = out_dir / f"noise_floor_verdict_audit_{tag}.json"
    jp.write_text(json.dumps(payload, ensure_ascii=False, indent=2))

    md = [
        f"# 잡음바닥 대비 판정 감사 — {stamp.strftime('%Y-%m-%d %H:%M KST')}",
        "",
        f"## 결론",
        f"- {verdict_line}",
        f"- 같은 런 안 짝 Δ 잡음: std **±{pair_std:.4f}** · 범위 {pair_range:.4f} · "
        f"문턱 초과 **{len(pair_over)}/{len(valid)}** 앵커(부호 일관 {pair.get('sign_consistent')})",
        f"- 역사적 '신호있음' {len(rows)}건 중 날짜 간 레벨 잡음바닥 초과 **{len(above_level)}건** · "
        f"짝 2σ 초과 **{len(above_pair)}건**",
        f"- 검정력: 문턱 +{thr:.2f} 를 2σ 로 분해하려면 짝 프로토콜 **앵커 {k_pair}개** / "
        f"날짜 간 레벨 비교 **앵커 {k_level}개** 필요",
        "",
        "## 1) 날짜 간 레벨 잡음바닥 (같은 모델, 앵커만 이동)",
        "",
        "| 앵커 이동(거래일) | " + " | ".join(level_noise.keys()) + " |",
        "|---|" + "---|" * len(level_noise),
        "| \\|Δ\\| (로버스트 AUC) | " + " | ".join(f"{v:.4f}" for v in level_noise.values()) + " |",
        "",
        f"→ 최대 |Δ| **{max_level_noise:.4f}** = 등록 문턱 +{thr:.2f} 의 **{max_level_noise / thr:.1f}배**.",
        "",
        "## 2) 무개선 카운터의 Δ vs 잡음바닥",
        "",
    ]
    if counter_delta is not None:
        md.append(f"- 스코어보드 헤드라인 로버스트 **{headline}** vs 등록 기준선 **{base}** "
                  f"→ Δ **{counter_delta:+.4f}**")
        md.append(f"- Δ / 잡음바닥 = **{counter_ratio:.2f}** → 카운터는 **측정 불가 구간**에서 "
                  f"19사이클을 셌다")
    else:
        md.append("- 헤드라인 미확인")
    md += [
        "",
        "## 3) 같은 런 짝 Δ 잡음 (두 모델 고정)",
        "",
        "| " + " | ".join(valid) + " |",
        "|---|" + "---|" * len(valid),
        "| 짝 Δ | " + " | ".join(f"{pd[a]:+.4f}" for a in valid if a in pd) + " |",
        "",
        f"평균 {pair.get('pair_delta_mean'):+.4f} · 방향 일관({pair.get('sign_consistent')}) · "
        f"문턱 초과 {len(pair_over)}/{len(valid)}",
        "",
        "## 4) 역사적 '신호있음' 판정의 잡음바닥 대비",
        "",
        "| id | Δ | vs 날짜간 레벨잡음 | vs 짝 2σ |",
        "|---|---|---|---|",
    ]
    for r in rows:
        dv = r.get("delta")
        md.append(f"| {r['id']} | {'-' if dv is None else f'{float(dv):+.4f}'} | "
                  f"{r.get('vs_level_noise') or '-'} | {r.get('vs_pair_2sigma') or '-'} |")
    md += [
        "",
        "## 함의 (판정 아님 · 측정정합성)",
        "1. `무개선 연속 N사이클` 은 **모델 축이 아니라 계측기 축의 잡음**을 셀 수 있다 — "
        "19사이클 무개선은 '새 레버 없음'과 '측정 불가'를 구분하지 못한다.",
        "2. 그래서 CG145(앵커 평균 프로토콜)와 CG131(청정 패널·배포가능 arm 기준선 이관)는 "
        "성능 변경이 아니라 **판정 가능성 복구**다.",
        "3. 이 감사는 등록 문턱·기준선을 **바꾸지 않는다**(리뷰보드 대상). 근거만 남긴다.",
    ]
    mp = out_dir / f"noise_floor_verdict_audit_{tag}.md"
    mp.write_text("\n".join(md) + "\n")

    print(json.dumps({k: v for k, v in payload.items() if k != "historical_signals"},
                     ensure_ascii=False, indent=2))
    print(f"\n[written] {jp}")
    print(f"[written] {mp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
