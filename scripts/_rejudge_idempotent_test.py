"""재판정(rejudge_parser_gap) 회귀 — '같은 판정을 매 틱 다시 쓰는' 루프를 막는 마커 검증.

배경(실측 2026-11-20 21:07 tick): '기준선없음'으로 남은 7건(CG10·CG323·CG329·CG330·CG332·CG336…)이
매 틱 다시 파싱·재판정되어 원장·백로그가 매시간 재기록되고 로그에 같은 줄이 14번 찍혔다.
마커(rejudge_stable{sig})를 남겨 같은 판정기 코드에서는 한 번만 재판정하도록 고쳤고,
판정기 코드(모듈 해시=서명)가 바뀌면 자동으로 다시 재판정되어야 한다.

실행: python3 scripts/_rejudge_idempotent_test.py   (실제 원장/백로그는 건드리지 않는다)
"""
import sys

sys.path.insert(0, "scripts")
import model_engineer_cycle as m  # noqa: E402

fails = []


def check(name, cond, extra=""):
    if cond:
        print(f"PASS {name}")
    else:
        print(f"FAIL {name} {extra}")
        fails.append(name)


def run_case():
    calls = {"parse": 0, "rewrite": 0, "save": 0}
    rows = [{"id": "X1", "verdict": "기준선없음", "detail": "d",
             # 실제 CG10·CG323 계열의 모양: 요약에 arm·대조군이 둘 다 있는데 이름 파싱이 실패한 기록.
             # 이 모양은 parsed 를 교체해도 gap 조건(verdict='기준선없음' + per_exp≥2)이 계속 성립해
             # 매 틱 재판정되는 것이 원래 증상이었다.
             "parsed": {"per_exp": {"A": {"auc": 0.53}, "B": {"auc": 0.51}}}}]
    backlog = {"items": [{"id": "X1", "command": "echo x", "metric": "m"}]}
    orig = {k: getattr(m, k) for k in
            ("load_ledger", "_rewrite_ledger", "load_backlog", "save_backlog",
             "parse_by_metric", "judge_by_metric", "_rejudge_sig")}
    try:
        m.load_ledger = lambda: rows
        m.load_backlog = lambda: backlog
        m._rewrite_ledger = lambda r: calls.__setitem__("rewrite", calls["rewrite"] + 1)
        m.save_backlog = lambda b: calls.__setitem__("save", calls["save"] + 1)

        def _parse(*a, **k):
            calls["parse"] += 1
            # 실제 파서처럼 arm·대조군 두 config 를 담은 요약을 돌려준다(이 모양이 유지돼야
            # gap 조건이 매 틱 성립하는 원래 증상을 재현할 수 있다).
            return {"per_exp": {"A": {"auc": 0.53}, "B": {"auc": 0.51}}}

        m.parse_by_metric = _parse
        m.judge_by_metric = lambda it, p: ("기준선없음", "d", None)  # 판정 불변
        m._rejudge_sig = lambda: "sigA"

        first = m.rejudge_parser_gap()
        check("1) 판정 불변이면 '변경'으로 보고하지 않는다", first == [], str(first))
        check("2) 마커가 남는다", isinstance(rows[0].get("rejudge_stable"), dict), str(rows[0].keys()))
        check("3) 마커에 코드 서명이 기록된다", rows[0]["rejudge_stable"].get("sig") == "sigA")

        before = calls["parse"]
        second = m.rejudge_parser_gap()
        check("4) 두 번째 틱은 재판정을 건너뛴다", second == [] and calls["parse"] == before,
              f"parse calls {before}->{calls['parse']}")

        m._rejudge_sig = lambda: "sigB"  # 판정기 코드가 바뀐 상황
        m.rejudge_parser_gap()
        check("5) 판정기 코드가 바뀌면 다시 재판정한다", calls["parse"] == before + 1,
              f"parse calls {calls['parse']}")

        m.judge_by_metric = lambda it, p: ("측정완료", "fixed", 0.03)  # 판정이 바뀌는 경우
        rows[0].pop("rejudge_stable", None)
        third = m.rejudge_parser_gap()
        check("6) 판정이 실제로 바뀌면 보고한다", [t[0] for t in third] == ["X1"], str(third))
        check("7) 판정이 바뀌면 마커를 지운다", "rejudge_stable" not in rows[0])
        check("8) 원장 재기록이 일어난다", calls["rewrite"] >= 1, str(calls))
    finally:
        for k, v in orig.items():
            setattr(m, k, v)


run_case()
print(f"\n{'ALL PASS' if not fails else 'FAILED: ' + ', '.join(fails)}")
sys.exit(1 if fails else 0)
