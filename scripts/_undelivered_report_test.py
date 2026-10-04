#!/usr/bin/env python3
"""_undelivered_report_test — '보고 처리했지만 전달되지 않은 결과' 감지 검증.

왜(실측 2026-09-30): 이 잡의 04:00·05:00·06:00·07:00 크론 실행이
`RuntimeError: Hermes can't reach the model provider` 로 실패했다. 틱 스크립트는 정상 실행됐으므로
04:51 완료된 U3 결과의 원장 `reported` 플래그는 켜졌지만 **사용자에게는 전달되지 않았다**.
원장 플래그만 믿으면 결과가 영구 소실된다 → 다음 틱이 executions.db 로 전달 결과를 확인해
그 실행이 소비한 기록을 미보고로 되돌려야 한다.

검증 항목(시나리오 12종):
  1) 전달 성공(delivered)  → 플래그 유지
  2) 전달 실패(failed)     → 플래그 되돌림 + 경고 메시지
  3) 결과 미확정(unknown)  → 플래그 되돌림
  4) reported_at 없는 옛 기록 → 손대지 않는다(창 밖)
  5) executions.db 없음    → NOTE 만, 플래그 유지(중복 보고 방지)
  6) 실행 진행 중(outcome None, status running) → 판정 보류, 플래그 유지
  7) 창 밖(reported_at 이 실패 실행보다 3시간 뒤) → 건드리지 않는다
  8) 여러 건 혼합: 실패 실행이 소비한 것만 되돌린다
  9) [2026-10-01 실측 추가] status='failed'·outcome=NULL('Interrupted by shutdown') → 되돌림
     종전 감지기는 outcome 만 봐서 이 형태(=호스트/세션 종료로 중단)를 통째로 건너뛰었다.
  10) status='failed'·outcome='delivered' → 되돌리지 않는다(전달된 결과 보호)
  11) reported_at 없음 + ts 가 창 안 → ts 로 대체 판정해 되돌림(수동 표시 경로 구제)
  12) 되돌림 대상이라도 실행이 lookback(6h)보다 오래됐으면 손대지 않는다(옛 상태 재보고 방지)
  13) [2026-10-05] 최신 실행이 delivered 인데 옛 실패 실행의 창(±3600s)이 같은 기록을 덮는 경우 →
      유지(틱이 매시간이라 창이 겹쳐 중복 보고되던 결함). 최신 실행의 전달 여부로 확정한다.
      반대로 최신 실행이 failed 면 되돌린다(13b).

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


def run(m, tmp, rows, ledger_rows, lookback=10 ** 6, ts=None):
    led, db = make_env(tmp, rows, ledger_rows)
    m.LEDGER = led
    m.HERMES_EXEC_DB = db
    m.cron_job_id = lambda: "testjob"
    m.UNDELIVERED_LOOKBACK_HOURS = lookback
    if ts:
        m.now_kst = ts
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

    # 4) reported_at 없는 옛 기록(ts 도 창 밖) → 손대지 않음
    with tempfile.TemporaryDirectory() as tmp:
        msgs, out = run(m, tmp, [("testjob", BASE, "failed", "failed")], [rec("OLD", None)])
        check("4) reported_at 없음 + ts 창 밖 → 손대지 않음", not msgs and out[0]["reported"] is True)

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

    # 9) 실제 사고 형태: status='failed'·outcome=NULL ('Interrupted by shutdown before terminal completion')
    #    실측 2026-10-01 18:00·19:00 틱. 종전 코드는 outcome 만 봐서 이 형태를 통째로 건너뛰었다.
    with tempfile.TemporaryDirectory() as tmp:
        msgs, out = run(m, tmp, [("testjob", BASE, "failed", None)],
                        [rec("CG56", "2026-09-30T05:00:10+09:00")])
        check("9) status=failed·outcome NULL(중단) → 되돌림", bool(msgs) and out[0]["reported"] is False,
              f"msgs={msgs} reported={out[0]['reported']}")

    # 10) status='failed' 라도 전달은 됐다면 보호한다(중복 보고 방지)
    with tempfile.TemporaryDirectory() as tmp:
        msgs, out = run(m, tmp, [("testjob", BASE, "failed", "delivered")],
                        [rec("CG56", "2026-09-30T05:00:10+09:00")])
        check("10) failed+delivered → 유지", not msgs and out[0]["reported"] is True)

    # 11) reported_at 이 없어도 ts 가 창 안이면 구제한다(세션이 플래그만 수동으로 켠 경로)
    with tempfile.TemporaryDirectory() as tmp:
        r = rec("MANUAL", None)
        r["ts"] = "2026-09-30T05:00:20+09:00"
        msgs, out = run(m, tmp, [("testjob", BASE, "failed", None)], [r])
        check("11) reported_at 없음 + ts 창 안 → 되돌림", bool(msgs) and out[0]["reported"] is False,
              f"msgs={msgs} reported={out[0]['reported']}")

    # 12) 되돌림 대상이라도 실행이 lookback 보다 오래됐으면 손대지 않는다(옛 상태 재보고 방지)
    with tempfile.TemporaryDirectory() as tmp:
        late = lambda: datetime(2026, 10, 1, 5, 0, 0, tzinfo=KST)     # 24시간 뒤
        msgs, out = run(m, tmp, [("testjob", BASE, "failed", None)],
                        [rec("CG56", "2026-09-30T05:00:10+09:00")], lookback=6, ts=late)
        check("12) lookback(6h) 초과 실행 → 유지", not msgs and out[0]["reported"] is True,
              f"msgs={msgs}")

    # 12b) 같은 조건에서 now 가 5시간 뒤면 lookback 안 → 되돌림
    with tempfile.TemporaryDirectory() as tmp:
        near = lambda: datetime(2026, 9, 30, 10, 0, 0, tzinfo=KST)
        msgs, out = run(m, tmp, [("testjob", BASE, "failed", None)],
                        [rec("CG56", "2026-09-30T05:00:10+09:00")], lookback=6, ts=near)
        check("12b) lookback(6h) 안 실행 → 되돌림", bool(msgs) and out[0]["reported"] is False)

    # 13) [2026-10-05 실측 추가] 나중 실행이 **전달에 성공**했는데 옛 실패 실행의 창(±3600s)이
    #     같은 기록을 덮어 중복 보고되던 결함. 틱이 매시간이라 창이 겹친다(04:01 failed·05:00
    #     delivered → reported_at=05:00:12). 최신 실행의 전달 여부로 확정해야 한다.
    with tempfile.TemporaryDirectory() as tmp:
        rows = [("testjob", "2026-09-30T05:00:11+09:00", "completed", "delivered"),
                ("testjob", "2026-09-30T04:01:01+09:00", "failed", None)]
        msgs, out = run(m, tmp, rows, [rec("CG105", "2026-09-30T05:00:12+09:00")])
        check("13) 최신 실행이 delivered 면 옛 실패 창에 걸려도 유지(중복 방지)",
              not msgs and out[0]["reported"] is True, f"msgs={msgs} reported={out[0]['reported']}")

    # 13b) 반대 순서: 최신 실행이 실패면 기록을 되돌린다(최신 판정이 실패를 놓치지 않는다)
    with tempfile.TemporaryDirectory() as tmp:
        rows = [("testjob", "2026-09-30T05:00:10+09:00", "failed", None),
                ("testjob", "2026-09-30T04:00:10+09:00", "completed", "delivered")]
        msgs, out = run(m, tmp, rows, [rec("CG105", "2026-09-30T05:00:20+09:00")])
        check("13b) 최신 실행이 failed 면 되돌림", bool(msgs) and out[0]["reported"] is False,
              f"msgs={msgs} reported={out[0]['reported']}")

    print(f"=== {'ALL PASS' if not FAILS else str(len(FAILS)) + ' FAIL'} ===")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
