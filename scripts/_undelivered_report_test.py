#!/usr/bin/env python3
"""_undelivered_report_test — '보고 처리했지만 전달되지 않은 결과' 감지 검증.

왜(실측 2026-09-30): 이 잡의 04:00·05:00·06:00·07:00 크론 실행이
`RuntimeError: Hermes can't reach the model provider` 로 실패했다. 틱 스크립트는 정상 실행됐으므로
04:51 완료된 U3 결과의 원장 `reported` 플래그는 켜졌지만 **사용자에게는 전달되지 않았다**.
원장 플래그만 믿으면 결과가 영구 소실된다 → 다음 틱이 executions.db 로 전달 결과를 확인해
그 실행이 소비한 기록을 미보고로 되돌려야 한다.

검증 항목(시나리오 6종):
  1) 전달 성공(delivered)  → 플래그 유지
  2) 전달 실패(failed)     → 플래그 되돌림 + 경고 메시지
  3) 결과 미확정(unknown)  → 플래그 되돌림
  4) reported_at 없는 옛 기록 → 건드리지 않는다(감지 대상 아님)
  5) executions.db 없음    → NOTE 만, 플래그 유지(중복 보고 방지)
  6) 실행 진행 중(outcome None, status running) → 판정 보류, 플래그 유지
  7) 창 밖(reported_at 이 실패 실행보다 3시간 뒤) → 건드리지 않는다

사용: python3 scripts/_undelivered_report_test.py
"""
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
KST = timezone(timedelta(hours=9))
FAILS = []


def load_driver():
    spec = importlib.util.spec_from_file_location(
        "model_engineer_cycle", os.path.join(HERE, "model_engineer_cycle.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def make_env(tmp, rows, ledger_rows):
    """임시 원장·임시 executions.db 를 만들고 구동기 상수를 그쪽으로 돌린다."""
    led = os.path.join(tmp, "ledger.jsonl")
    with open(led, "w", encoding="utf-8") as f:
        for r in ledger_rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    db = os.path.join(tmp, "executions.db")
    if rows is not None:
        con = sqlite3.connect(db)
        con.execute("create table executions (job_id text, started_at text, status text,"
                    " delivery_outcome text)")
        con.executemany("insert into executions values (?,?,?,?)", rows)
        con.commit()
        con.close()
    else:
        db = os.path.join(tmp, "no_such.db")
    return led, db


def rec(rid, reported_at, reported=True):
    return {"ts": "2026-09-30T04:51:00+09:00", "id": rid, "title": f"{rid} 실험", "rc": 0,
            "elapsed_min": 496.0, "log": "x.log", "verdict": "노이즈", "detail": "d",
            "parsed": {"per_exp": {"LS_quant_q30_h5": {"mean": 0.5557, "std": 0.0271,
                                                       "fold_win_rate": 1.0, "folds": []}}},
            "reported": reported, **({"reported_at": reported_at} if reported_at else {})}


def run(m, tmp, rows, ledger_rows):
    led, db = make_env(tmp, rows, ledger_rows)
    m.LEDGER = led
    m.HERMES_EXEC_DB = db
    m.cron_job_id = lambda: "testjob"
    msgs = m.check_undelivered_reports()
    out = [json.loads(l) for l in open(led, encoding="utf-8") if l.strip()]
    return msgs, out


def check(name, cond, extra=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {extra}" if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


BASE = "2026-09-30T05:00:00+09:00"      # 실패한 크론 실행의 시작 시각(KST)


def main():
    m = load_driver()
    print("=== 미전달 보고 감지 ===")

    # 1) 전달 성공
    with tempfile.TemporaryDirectory() as tmp:
        msgs, out = run(m, tmp, [("testjob", BASE, "completed", "delivered")],
                        [rec("U3", "2026-09-30T05:00:10+09:00")])
        check("1) delivered → 되돌리지 않음", not msgs and out[0]["reported"] is True,
              f"msgs={msgs} reported={out[0]['reported']}")

    # 2) 전달 실패
    with tempfile.TemporaryDirectory() as tmp:
        msgs, out = run(m, tmp, [("testjob", BASE, "failed", "failed")],
                        [rec("U3", "2026-09-30T05:00:10+09:00")])
        check("2) failed → 미보고로 되돌림", bool(msgs) and out[0]["reported"] is False
              and "reported_at" not in out[0], f"msgs={msgs} rec={out[0]}")
        check("2b) 경고에 id 가 들어간다", any("U3" in x for x in msgs), str(msgs))

    # 3) 결과 미확정
    with tempfile.TemporaryDirectory() as tmp:
        msgs, out = run(m, tmp, [("testjob", BASE, "unknown", "unknown")],
                        [rec("CG38", "2026-09-30T05:00:05+09:00")])
        check("3) unknown → 되돌림(CG38 소실 방지)", bool(msgs) and out[0]["reported"] is False)

    # 4) reported_at 없는 옛 기록
    with tempfile.TemporaryDirectory() as tmp:
        msgs, out = run(m, tmp, [("testjob", BASE, "failed", "failed")], [rec("OLD", None)])
        check("4) reported_at 없음 → 손대지 않음", not msgs and out[0]["reported"] is True)

    # 5) DB 없음 → NOTE, 플래그 유지
    with tempfile.TemporaryDirectory() as tmp:
        msgs, out = run(m, tmp, None, [rec("U3", "2026-09-30T05:00:10+09:00")])
        check("5) executions.db 없음 → NOTE + 플래그 유지",
              len(msgs) == 1 and msgs[0].startswith("NOTE") and out[0]["reported"] is True,
              f"msgs={msgs}")

    # 6) 실행 진행 중 → 판정 보류
    with tempfile.TemporaryDirectory() as tmp:
        msgs, out = run(m, tmp, [("testjob", BASE, "running", None)],
                        [rec("U3", "2026-09-30T05:00:10+09:00")])
        check("6) 진행 중(outcome None) → 보류", not msgs and out[0]["reported"] is True)

    # 7) 창 밖 — 실패 실행보다 3시간 뒤에 보고 처리된 기록은 그 실행이 소비한 것이 아니다
    with tempfile.TemporaryDirectory() as tmp:
        msgs, out = run(m, tmp, [("testjob", BASE, "failed", "failed")],
                        [rec("LATE", "2026-09-30T08:00:00+09:00")])
        check("7) ±창 밖 → 손대지 않음", not msgs and out[0]["reported"] is True)

    # 8) 여러 건 혼합: 실패 실행이 소비한 것만 되돌린다
    with tempfile.TemporaryDirectory() as tmp:
        msgs, out = run(m, tmp, [("testjob", BASE, "failed", "failed")],
                        [rec("A", "2026-09-30T05:00:20+09:00"),
                         rec("B", "2026-09-30T05:00:25+09:00"),
                         rec("C", "2026-09-30T12:00:00+09:00")])
        by = {r["id"]: r for r in out}
        check("8) 배치 중 실패분만 되돌림", by["A"]["reported"] is False
              and by["B"]["reported"] is False and by["C"]["reported"] is True)

    print(f"=== {'ALL PASS' if not FAILS else str(len(FAILS)) + ' FAIL'} ===")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
