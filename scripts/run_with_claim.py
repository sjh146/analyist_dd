#!/usr/bin/env python3
"""run_with_claim — 러너를 감싸서 "자기신고 vs 실제 적재량"을 대조 기록한다.

WHY: 러너가 12개 넘고 대부분 크론으로 무인 실행된다. 2026-09-24 수급 확장 러너가 파서 키
불일치(output2→output)로 **+0행을 적재하고도 exit 0** 으로 끝났고, 진행 파일에는 "완료"로
기록돼 그 종목이 조용히 스킵됐다. 그때는 사후에 손으로 DB 를 세어보기 전까지 알 수 없었다.
러너마다 코드를 고치는 대신 **감싸는 방식**으로 같은 보증을 모든 러너에 준다.

사용:
    /usr/bin/python3 scripts/run_with_claim.py \
        --runner kis_short_selling_backfill --table krx_short_selling \
        -- /usr/bin/python3 scripts/kis_short_selling_backfill.py --days 5

동작 순서:
  1) 실행 전 테이블 행수 스냅샷
  2) 자식 프로세스 실행 — stdout/stderr 를 그대로 흘려보내며 캡처
  3) 실행 후 행수 스냅샷 → persisted = 델타
  4) 자식 출력에서 자기신고 수치 추출(정규식, 기본은 "+N행/+N건" 합계) → claimed
  5) dq_runner_claim 에 기록. gap != 0 이면 경고.

설계 원칙:
  - **자식의 exit code 를 그대로 반환**한다. 신고 실패가 수집 성공을 실패로 바꾸면 안 된다.
  - 신고 수치를 못 읽으면 claimed = persisted 로 기록하고 note 에 NOT_PARSED 를 남긴다
    (오탐 gap 을 만들지 않되, 형식 미인식 사실은 드러낸다).
  - 테이블명은 화이트리스트 정규식으로 검증한다(COUNT(*) 문자열 결합이므로).
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dq_claim import record_claim  # noqa: E402

TABLE_RE = re.compile(r"^[a-z_][a-z0-9_]*$")

# 기본 자기신고 패턴 — 우리 러너들이 실제로 찍는 형식들
DEFAULT_PATTERNS = [
    re.compile(r"\+\s*([0-9][0-9,]*)\s*(?:행|건|rows?)", re.I),          # "+1,234행"
    re.compile(r"(?:적재|저장|추가|신규|완료)\s*[:=]?\s*([0-9][0-9,]*)\s*(?:행|건)"),  # "적재 123행"
    re.compile(r"([0-9][0-9,]*)\s*(?:행|건)\s*(?:적재|저장|추가|완료)"),  # "123행 적재"
]


def _pg_connect():
    import psycopg2

    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.environ.get("POSTGRES_PORT", "5434")),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )


def _count(conn, table: str) -> int:
    cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM " + table)  # noqa: S608 - TABLE_RE 로 검증됨
        return int(cur.fetchone()[0])
    finally:
        cur.close()


def parse_claim(text: str, patterns) -> int | None:
    """출력에서 자기신고 수치를 뽑는다. 여러 줄이면 합계(러너들이 종목별 +N행을 찍는다)."""
    total = 0
    found = False
    for pat in patterns:
        for m in pat.finditer(text):
            total += int(m.group(1).replace(",", ""))
            found = True
        if found:
            break
    return total if found else None


def main() -> int:
    ap = argparse.ArgumentParser(description="러너 자기신고 래퍼")
    ap.add_argument("--runner", required=True, help="러너 이름(메트릭 라벨)")
    ap.add_argument("--table", required=True, help="적재 대상 테이블")
    ap.add_argument("--claim-regex", default=None, help="자기신고 추출 정규식(그룹1=숫자)")
    ap.add_argument("--note", default="", help="note 에 덧붙일 문구")
    ap.add_argument("cmd", nargs=argparse.REMAINDER, help="-- 뒤에 실행할 명령")
    args = ap.parse_args()

    cmd = [c for c in args.cmd if c != "--"]
    if not cmd:
        ap.error("실행할 명령이 없습니다 (-- 뒤에 지정)")

    if not TABLE_RE.match(args.table):
        print(f"run_with_claim: 테이블명 거부: {args.table!r}", file=sys.stderr)
        return 2

    patterns = DEFAULT_PATTERNS
    if args.claim_regex:
        patterns = [re.compile(args.claim_regex)]

    conn = _pg_connect()
    try:
        before = _count(conn, args.table)
        print(f"[claim] {args.runner} → {args.table} 실행 전 {before}행", flush=True)

        proc = subprocess.run(cmd, capture_output=True, text=True)
        out = (proc.stdout or "") + (proc.stderr or "")
        sys.stdout.write(proc.stdout or "")
        sys.stderr.write(proc.stderr or "")

        after = _count(conn, args.table)
        persisted = after - before
        claimed = parse_claim(out, patterns)

        note = f"exit={proc.returncode} rows {before}->{after}"
        if args.note:
            note += " " + args.note

        if claimed is None:
            note += " NOT_PARSED(자기신고 미검출)"
            print("[claim] 경고: 자식 출력에서 자기신고 수치를 못 찾음 — "
                  "--claim-regex 로 형식을 지정하세요", file=sys.stderr)

        # 주의: 래퍼는 '소스가 준 행수'를 알 수 없다(자식 출력의 자기신고만 본다).
        # 그래서 source_rows 에 자기신고값을 넣어 parse_failure 를 만들지 않는다(오탐 방지).
        # 정확한 source/stored 분리는 러너 내부 배선(kis_supply_*)이 담당한다.
        stored = persisted if claimed is None else claimed
        record_claim(conn, args.runner, args.table,
                     claimed_rows=stored, persisted_rows=persisted,
                     source_rows=(None if claimed is None else claimed), note=note)

        if claimed is not None and claimed > 0 and persisted == 0:
            state = "신규 없음(같은 구간 재수집이면 정상 — 중복 upsert)"
        elif claimed is not None and claimed != persisted:
            state = f"차이 {claimed - persisted:+d}행 (재수집 중복 여부 확인)"
        else:
            state = "신규 적재 일치"
        print(f"[claim] 자기신고 {claimed}행 / 신규 {persisted}행 — {state}", flush=True)
        return proc.returncode
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
