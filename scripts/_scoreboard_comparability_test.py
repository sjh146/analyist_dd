#!/usr/bin/env python3
"""CG104 자체점검 — 스코어보드 best_robust 대조 가능성(comparability) 필터.

왜: 종전 best_robust 는 **전 이력 모든 arm 의 최고 mean** 이라 라벨 kind·q·게이트가
기준선과 다른 arm 이 섞여 거짓 돌파를 만들었다(실측 2026-10-05: CG89 Q5s_120_150 0.5870
(q0.05·슬라이스) 이 최댓값 → Δ+0.0464 '개선'으로 표기). 리뷰보드 승인(2026-10-05 22:4x) 후
기준선 태그(kind·q·core_only·horizon·유니버스슬라이스)와 일치하는 arm 만 계상한다.

실행: python3 scripts/_scoreboard_comparability_test.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import quant_scoreboard as q  # noqa: E402

FAIL = []


def check(name: str, cond: bool, detail: str = ""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        FAIL.append(name)


def main() -> int:
    # ── 1. 태그 레지스트리는 **import 없이**(numpy 부재 호스트) AST 로 읽힌다 ──────────
    reg = q._arm_tags_registry()
    check("1a 레지스트리 파싱(>100 config)", len(reg) > 100, f"n={len(reg)}")
    check("1b 기준선 arm 존재", q.BASELINE_ARM in reg, q.BASELINE_ARM)
    check("1c numpy 미import 로도 동작", "numpy" not in sys.modules, "sys.modules")

    # ── 2. 판정 규칙 ─────────────────────────────────────────────────────────────
    P = {"kind": "quantile", "horizon": 5}
    check("2a 기준선 arm → 대조가능",
          q.arm_comparability("LS_quant_q30_h5", P) == (True, ""))
    ok, why = q.arm_comparability("Q5s_120_150", P)          # q0.05 · 게이트 ON · 슬라이스
    check("2b q0.05 슬라이스 arm → 대조 불가", ok is False and "q 0.05" in why, why)
    ok, why = q.arm_comparability("US_00_30", P)             # q0.30 이지만 게이트 ON
    check("2c 게이트 ON arm → 대조 불가", ok is False and "게이트" in why, why)
    ok, why = q.arm_comparability("AT_00_30", {})              # 절대임계 라벨(레지스트리 kind)
    check("2d 라벨 kind 상이 → 대조 불가", ok is False and "kind" in why, why)
    ok, why = q.arm_comparability("TR_rank_h3", {"kind": "quantile", "horizon": 3})
    check("2e 호라이즌 상이 → 대조 불가", ok is False and "h3" in why, why)
    ok, why = q.arm_comparability("NOPE_x", P)               # 미등록 config
    check("2f 미등록 arm → 미분류(None, 종전대로 계상)", ok is None, why)
    ok, why = q.arm_comparability("LS_quant_q30_h5", P,
                                  {"panel": "/app/app/models/wf/panel_995.npz", "days": 995, "limit": 50})
    check("2g 유니버스(패널·days) 상이 → 대조 불가", ok is False and "유니버스" in why, why)
    ok, why = q.arm_comparability("LS_quant_q30_h5", P,
                                  {"panel": "/app/app/models/wf/panel_420_asofpatch.npz", "days": 420, "limit": 50})
    check("2h 기준선 유니버스 → 대조가능", ok is True, why)

    # ── 3. _rec_best_mean 이 대조 불가 arm 을 제외한다 ─────────────────────────────
    incmp = {"parsed": {"per_exp": {"Q5s_120_150": {"mean": 0.99, "kind": "quantile", "horizon": 5}}}}
    check("3a 대조 불가만 있는 기록 → None", q._rec_best_mean(incmp) is None)

    mixed = {"parsed": {"per_exp": {
        "Q5s_120_150": {"mean": 0.99, "kind": "quantile", "horizon": 5},   # 제외
        "LS_quant_q30_h5": {"mean": 0.55, "kind": "quantile", "horizon": 5},  # 포함
        "ZZZ_new": {"mean": 0.60, "kind": "quantile", "horizon": 5},       # 미분류 → 포함
    }}}
    check("3b 혼합 기록 → 대조가능/미분류 최고값", q._rec_best_mean(mixed) == 0.60,
          str(q._rec_best_mean(mixed)))

    # ── 4. 실 원장: 거짓 돌파가 사라지고 기준선 값이 보존된다 ────────────────────────
    s = q.engineer_stanza()
    check("4a 기준선 값 보존", q.BASELINE_ROBUST == 0.5406, str(q.BASELINE_ROBUST))
    check("4b best_robust 가 거짓 돌파(q0.05 슬라이스 0.5870)가 아니다",
          s.get("best_robust") != 0.587, str(s.get("best_robust")))
    check("4c 최고 대조불가 arm 이 각주로 남는다",
          (s.get("best_excluded") or {}).get("arm") == "Q5s_120_150",
          str(s.get("best_excluded")))
    check("4d comparability 블록 존재", isinstance(s.get("comparability"), dict))
    check("4e 대조가능 arm 이 0개가 아니다",
          (s.get("comparability") or {}).get("arms_comparable", 0) > 0,
          str((s.get("comparability") or {}).get("arms_comparable")))
    check("4f 무개선 카운터가 측정수 이하",
          s.get("no_improve_cycles", 0) <= s.get("measured_cycles", 0),
          f"{s.get('no_improve_cycles')}/{s.get('measured_cycles')}")
    # 4g: best_robust 는 어떤 기록에서 왔든 '대조 가능'이어야 한다
    okb, whyb = q.arm_comparability(str(s.get("best_exp")), {"kind": "quantile", "horizon": 5})
    check("4g best_exp 는 대조가능 arm", okb is not False,
          f"{s.get('best_exp')} ok={okb} {whyb}")

    print(f"\n{'ALL PASS' if not FAIL else 'FAILED: ' + ', '.join(FAIL)}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
