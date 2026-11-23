#!/usr/bin/env python3
"""저녁 파이프라인(20:00) 컨테이너 phase 의 자기신고를 '실제 행 델타'로 남긴다 (R28).

WHY (2026-11-23 실측):
    `full_pipeline_dd.sh` 의 컨테이너 phase(1-3 뉴스, 1-4 경제일정, 1-5 재무, 1-6 벡터)
    는 `docker exec` 로 돌아 러너 내부 배선(claim_start/claim_finish)을 쓸 수 없고,
    `[claim]` 줄도 찍지 않는다 → 수집이 실패해도 `dq_runner_claim` 에 아무 흔적이 없어
    다음 날까지 침묵한다(R27 원칙: 배선 문자열이 아니라 실제 행).

설계:
    · 호스트 래퍼가 phase **전/후** 대상 테이블의 `count(*)` 를 재고, 이 스크립트가 델타를
      계산해 `dq_claim.record_claim` 한 행으로 남긴다. 행수는 지어내지 않는다(측정값만).
    · `source_rows` 는 NULL 로 둔다 — 소스가 준 행수를 모르기 때문. 그 결과
      parse_failure(=source>0 & claimed==0)는 절대 울리지 않는다(오탐 금지).
    · 자기신고 실패가 수집을 깨면 안 된다 → 무슨 일이 있어도 exit 0.
    · 규약: claimed_rows 는 **델타(신규 행)**. upsert phase 라 델타가 0 일 수 있고,
      그건 실패가 아니다(그래서 zero_stored 는 알림 대상이 아니다).

사용:
    phase_claim.py --runner pipeline_news --table news_analysis --before 100 --after 130 --rc 0
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def compute_delta(before, after) -> int | None:
    """두 count 를 받아 델타를 돌려준다. 하나라도 못 읽었으면 None(지어내지 않는다)."""
    try:
        b = int(before)
        a = int(after)
    except (TypeError, ValueError):
        return None
    return a - b


def build_note(rc, before, after) -> str:
    if rc in (None, ""):
        rc_txt = "?"
    else:
        try:
            rc_txt = str(int(rc))
        except (TypeError, ValueError):
            rc_txt = str(rc)
    return "evening_pipeline phase: rc={0} count {1}->{2}".format(rc_txt, before, after)


def record(runner: str, table: str, before, after, rc=None, note: str | None = None) -> bool:
    delta = compute_delta(before, after)
    if delta is None:
        print("[phase_claim] count 미확인({0}->{1}) — 자기신고 생략".format(before, after),
              file=sys.stderr)
        return False
    claimed = delta if delta >= 0 else 0
    try:
        from dq_claim import _open_conn, record_claim
    except Exception as exc:  # noqa: BLE001 — 배선 부재가 수집을 깨면 안 된다
        print("[phase_claim] dq_claim import 실패: {0}".format(exc), file=sys.stderr)
        return False
    conn = None
    try:
        conn = _open_conn()
        if conn is None:
            print("[phase_claim] DB 연결 실패 — 자기신고 생략", file=sys.stderr)
            return False
        record_claim(
            conn, runner, table, claimed_rows=claimed,
            persisted_rows=delta, source_rows=None,
            note=note or build_note(rc, before, after),
        )
        return True
    except Exception as exc:  # noqa: BLE001
        print("[phase_claim] 기록 실패: {0}".format(exc), file=sys.stderr)
        return False
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:  # noqa: BLE001
            pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="저녁 파이프라인 phase 자기신고(행 델타)")
    ap.add_argument("--runner", required=True)
    ap.add_argument("--table", required=True)
    ap.add_argument("--before", default="")
    ap.add_argument("--after", default="")
    ap.add_argument("--rc", default="")
    ap.add_argument("--note", default=None)
    args = ap.parse_args(argv)
    ok = record(args.runner, args.table, args.before, args.after, args.rc, args.note)
    print("[phase_claim] {0} {1} delta={2} ok={3}".format(
        args.runner, args.table, compute_delta(args.before, args.after), ok))
    return 0            # 규약: 자기신고는 절대 파이프라인 rc 를 바꾸지 않는다


if __name__ == "__main__":
    raise SystemExit(main())
