#!/usr/bin/env python3
"""스코어보드 기준선·유니버스 이관의 **오프라인 반사실(counterfactual) dry-run** (CG131 근거).

무엇을 하나:
  `scripts/quant_scoreboard.py` 를 **읽기 전용으로 import** 해 기준선 상수만 바꿔 끼우고
  engineer 스탠자를 두 번 계산한다 — ① 현행(누수 패널 panel_420_asofpatch·0.5406)
  ② 이관안(청정 패널 panel_prod200·리뷰보드 승인 시 등록할 값).

왜 필요한가:
  CG131 은 "등록 기준선·헤드라인 arm 필터를 청정 패널로 이관"하는 **리뷰보드 승인 항목**이다.
  승인 직후 무엇이 어떻게 바뀌는지(헤드라인·Δ·무개선 꼬리연속)를 숫자로 미리 제시해야
  승인자가 판단할 수 있다. 실제 스코어보드 파일(quant_scoreboard.py)은 **건드리지 않는다** —
  상수 대입은 이 프로세스 안에서만 일어나고 OUT_JSON/OUT_MD 도 쓰지 않는다(engineer_stanza 만 호출).

주의:
  · 이 도구의 출력은 **계측기 값**이지 성능 판정이 아니다(판정은 실험 원장의 verdict).
  · 이관안 기준선 값은 청정 패널 실측(CG87/CG130)에서 온다 — 하드코딩하지 않고 원장에서 찾는다.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QSB = os.path.join(PROJ, "scripts", "quant_scoreboard.py")


def load_qsb():
    """quant_scoreboard 를 모듈로 로드한다(main() 은 __main__ 가드라 실행되지 않는다)."""
    spec = importlib.util.spec_from_file_location("quant_scoreboard", QSB)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def clean_panel_baseline(qsb, arm, panel_key, days, limit):
    """청정 패널 원장 기록에서 기준선 arm 의 폴드평균을 찾는다(하드코딩 금지).

    반환: (mean, rec_id, ts, folds) — 없으면 (None, ...).
    """
    for rec in qsb._jsonl(qsb.ME_LEDGER):
        cfg = ((rec.get("parsed") or {}).get("config") or {})
        p = str(cfg.get("panel") or "").rsplit("/", 1)[-1]
        if p != panel_key:
            continue
        per = ((rec.get("parsed") or {}).get("per_exp") or {})
        v = per.get(arm)
        if isinstance(v, dict) and isinstance(v.get("mean"), (int, float)):
            return round(float(v["mean"]), 10), rec.get("id"), rec.get("ts"), v.get("folds")
    return None, None, None, None


def headline(qsb, baseline, univ=None):
    """기준선·유니버스를 끼워 engineer 스탠자를 계산한다(캐시 초기화 필수)."""
    qsb.BASELINE_ROBUST = baseline
    qsb._UNIV_CACHE = dict(univ) if univ else None
    qsb._ARM_TAGS_CACHE = None
    return qsb.engineer_stanza()


def arm_table(qsb, panel_key, limit=None, baseline_tags=None):
    """원장 전체에서 (arm, panel, mean, std, ok, why) 를 모아 청정 패널 arm 만 표로 낸다."""
    rows = []
    for rec in qsb._jsonl(qsb.ME_LEDGER):
        cfg = ((rec.get("parsed") or {}).get("config") or {})
        panel = str(cfg.get("panel") or "?").rsplit("/", 1)[-1]
        if panel != panel_key:
            continue
        if limit is not None and cfg.get("limit") != limit:
            continue
        for arm, val, ok, why in qsb._rec_arm_entries(rec):
            rows.append({"arm": arm, "mean": round(float(val["mean"]), 4),
                         "std": (round(float(val["std"]), 4)
                                 if isinstance(val.get("std"), (int, float)) else None),
                         "ok": ok, "why": why, "rec": rec.get("id"), "ts": rec.get("ts"),
                         "limit": cfg.get("limit"), "days": cfg.get("days")})
    rows.sort(key=lambda r: -r["mean"])
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--panel", default="panel_prod200.npz", help="이관 대상 청정 패널")
    ap.add_argument("--days", type=int, default=420)
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--arm", default="LS_quant_q30_h5", help="기준선 arm 이름")
    ap.add_argument("--gate-on", action="store_true",
                    help="이관 기준선을 프로덕션 경로(게이트 ON·core_only=True)로 함께 바꾼다 — "
                         "종전 필터는 게이트 OFF arm 만 계상하므로 CO_* arm 이 전부 '대조 불가'가 된다")
    ap.add_argument("--json-out", default=None)
    a = ap.parse_args()

    qsb = load_qsb()
    out = {"current": None, "migrated": None, "panel_arms": [],
           "proposed": {"panel": a.panel, "days": a.days, "limit": a.limit,
                        "arm": a.arm, "core_only": bool(a.gate_on)}}

    # ① 현행(누수 패널) — 태그를 바꾸기 **전에** 계산해야 한다
    cur = headline(qsb, 0.5406)
    out["current"] = {
        "baseline": cur["baseline"], "best_robust": cur["best_robust"],
        "best_exp": cur["best_exp"], "best_rec_id": cur["best_rec_id"],
        "delta": cur["delta"], "no_improve_streak": cur["no_improve_streak"],
        "panels_comparable": (cur.get("comparability") or {}).get("panels_comparable"),
        "arms_comparable": (cur.get("comparability") or {}).get("arms_comparable"),
    }

    # ② 이관안 — 기준선 값을 청정 패널 원장에서 찾는다
    mean, rec_id, ts, folds = clean_panel_baseline(qsb, a.arm, a.panel, a.days, a.limit)
    # 기준선 태그 이관(게이트 ON) — 승인 대상은 '값'(0.5406)과 '필터'(core_only) 둘 다다.
    if a.gate_on:
        qsb.BASELINE_ARM = a.arm
        qsb.BASELINE_TAGS = {"kind": "quantile", "q": 0.30, "core_only": True, "horizon": 5}
    if mean is None:
        print(f"[FAIL] 원장에서 청정 패널 {a.panel} · {a.arm} 기록을 찾지 못했다 — "
              f"기준선을 등록할 수 없다(이관 전에 같은 프로토콜 재측정이 필요).")
        return 2
    univ = {"panel": a.panel, "days": a.days, "limit": a.limit}
    mig = headline(qsb, mean, univ)
    out["migrated"] = {
        "baseline": mig["baseline"], "baseline_source": {"rec": rec_id, "ts": ts, "folds": folds},
        "best_robust": mig["best_robust"], "best_exp": mig["best_exp"],
        "best_rec_id": mig["best_rec_id"], "delta": mig["delta"],
        "no_improve_streak": mig["no_improve_streak"],
        "no_improve_cycles": mig["no_improve_cycles"],
        "measured_cycles": mig.get("measured_cycles"),
        "panels_comparable": (mig.get("comparability") or {}).get("panels_comparable"),
        "arms_comparable": (mig.get("comparability") or {}).get("arms_comparable"),
        "best_excluded": mig.get("best_excluded"),
    }
    out["panel_arms"] = arm_table(qsb, a.panel, a.limit)

    print(f"[CG131 dry-run] 기준선 이관 반사실 — 파일 변경 없음(읽기 전용)")
    print(f"  현행   : 기준선 {out['current']['baseline']} ({out['current']['panels_comparable']}) "
          f"→ 헤드라인 {out['current']['best_robust']} ({out['current']['best_exp']}) "
          f"Δ{out['current']['delta']} · 무개선 꼬리 {out['current']['no_improve_streak']}")
    print(f"  이관안 : 기준선 {out['migrated']['baseline']} ({a.panel}·L{a.limit}) "
          f"[{rec_id} {ts}] → 헤드라인 {out['migrated']['best_robust']} "
          f"({out['migrated']['best_exp']}) Δ{out['migrated']['delta']} · "
          f"무개선 꼬리 {out['migrated']['no_improve_streak']}")
    print(f"  청정 패널 대조가능 arm {out['migrated']['arms_comparable']}개 · "
          f"패널 {out['migrated']['panels_comparable']}")
    print(f"  ── {a.panel} arm 표 (mean desc) ──")
    for r in out["panel_arms"][:20]:
        print(f"   {r['mean']:.4f} ±{r['std'] if r['std'] is not None else 'n/a':<7} "
              f"{r['arm']:<24} ok={r['ok']} {r['rec']} {r['why']}")

    if a.json_out:
        with open(a.json_out, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(f"  saved: {a.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
