#!/usr/bin/env python3
"""판정기 반환 형식 회귀 — tick 이 판정기 하나 때문에 통째로 죽지 않는지 잠근다.

왜(실측 2026-11-19 19:11 tick, rc=1): judge_date_paired_auc 만 2-튜플을 반환했고
rejudge_parser_gap 이 `verdict, detail, delta = judge_by_metric(...)` 로 풀다가
ValueError: not enough values to unpack (expected 3, got 2) 로 **tick 전체가 죽었다**.
그 밤 큐는 어떤 항목도 시작하지 못했고, 원장 재판정도 중간에 멈췄다.

검사:
  ① 모든 judge_* 가 (verdict, detail, delta) 3-튜플을 반환한다(정적 AST 검사 — 호출 없이 전수)
  ② _judge3 이 2/3/1/비튜플 반환을 흡수한다
  ③ parse_oos_accrual 이 이 런의 측정 sd 를 싣고, judge_oos_accrual 이 그 값을 SE 로 쓴다
     (상수 0.0587 고정이면 종목수 80→200 개선이 판정문에 보이지 않는다)
"""
import ast
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "model_engineer_cycle.py")
PROJ = os.path.dirname(HERE)
fails = []


def check(name, cond, msg=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}{'' if cond else ' — ' + msg}")
    if not cond:
        fails.append(name)


def load():
    spec = importlib.util.spec_from_file_location("mec", SRC)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def main():
    # ① 정적 검사 — 모든 judge_* 의 Return 이 2-튜플이면 실패(예외 없이 전수 확인)
    tree = ast.parse(open(SRC, encoding="utf-8").read())
    bad = []
    for node in tree.body:
        if not (isinstance(node, ast.FunctionDef) and node.name.startswith("judge_")):
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Return) and isinstance(sub.value, ast.Tuple):
                if len(sub.value.elts) == 2:
                    bad.append((node.name, sub.lineno))
    check("judge_* 반환에 2-튜플 없음", not bad, f"2-튜플 반환: {bad}")

    m = load()

    # ② 정규화
    check("_judge3 3-튜플 그대로", m._judge3(("a", "b", 1.0)) == ("a", "b", 1.0))
    check("_judge3 2-튜플 흡수", m._judge3(("a", "b")) == ("a", "b", None))
    check("_judge3 1-튜플 흡수", m._judge3(("a",)) == ("a", "", None))
    check("_judge3 비튜플 흡수", m._judge3("x") == ("x", "", None))

    # 날짜 짝 판정기 — 결측 조합에서 예외 없이 3-튜플
    cases = [
        ({"error": "e"}, ("판정불가", "e", None)),
        ({"paired": {"n": 3, "delta_mean": 0.01}}, None),
        ({"paired": {"n": 20, "delta_mean": 0.011, "se": 0.03}}, None),
        ({"paired": {"n": 20, "delta_mean": 0.011}}, None),      # se/sd/t 전부 결측
        ({"paired": {"n": 20, "delta_mean": 0.031, "se": 0.004,
                     "delta_sd": 0.02, "t": 7.7, "pos": 17, "neg": 3,
                     "sign_test_p": 0.0009, "boot_lo": 0.02, "boot_hi": 0.04}}, None),
    ]
    ok = True
    errs = []
    for parsed, expect in cases:
        try:
            r = m.judge_date_paired_auc({}, parsed)
        except Exception as e:                                  # noqa: BLE001
            ok = False
            errs.append(f"{parsed} → {type(e).__name__}: {e}")
            continue
        if not isinstance(r, tuple) or len(r) != 3:
            ok = False
            errs.append(f"{parsed} → {r!r} (3-튜플 아님)")
    check("judge_date_paired_auc 결측 조합에서 3-튜플", ok, "; ".join(errs))

    # ③ OOS 적립 — 측정 sd 반영
    p = os.path.join(PROJ, "services/xgboost-ml/reports/overnight/cg360_champ200.json")
    if os.path.exists(p):
        parsed = m.parse_oos_accrual(p, 0.0)
        check("parse_oos_accrual sd_measured 실림", isinstance(parsed.get("sd_measured"), float),
              f"sd_measured={parsed.get('sd_measured')}")
        check("측정 SE 가 상수 SE 와 구분됨",
              parsed.get("se_measured") is not None
              and parsed.get("se_measured") != parsed.get("se_per_date"),
              f"measured={parsed.get('se_measured')} const={parsed.get('se_per_date')}")
        it = {"metric": "oos_accrual", "baseline_dates": 22, "target_dates": 26}
        v, d, delta = m._judge3(m.judge_oos_accrual(it, parsed))
        check("판정문이 측정 sd 를 인용", "측정 sd" in d, d[:80])
    else:
        print("  [SKIP] cg360 요약 없음 — 측정 sd 검사 생략")

    print(f"\n{len(fails)} 실패" + ("" if not fails else f": {fails}"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
