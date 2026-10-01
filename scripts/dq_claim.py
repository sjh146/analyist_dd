"""러너 자기신고(claim) 헬퍼 — "적재했다"는 주장과 실제를 대조 가능하게 만든다.

WHY (2026-09-24 실측): 수급 확장 러너가 파서 키 불일치로 **+0행을 적재하고도 exit 0**
으로 성공 종료했다. 진행 파일에는 "완료"로 기록되어 그 종목은 영구 스킵됐고, 로그에는
실패가 없었다. 그때는 사후에 손으로 DB 를 세어보기 전까지 알 수 없었다.

이 모듈은 그 실패 유형을 메트릭으로 바꾼다:
  - ``claimed``   = 러너가 소스에서 받았다고 믿는 행수(수집기가 돌려준 행수)
  - ``persisted`` = 러너가 실제로 DB 에 쓴 행수
  - ``gap``       = claimed - persisted  (0 이 아니면 파싱/적재 버그)
postgres-exporter 의 ``dq_claim_gap`` 메트릭이 이 표를 읽고, Prometheus 알림
``RunnerClaimGap`` 이 0 이 아니면 울린다.

사용 (호스트 /usr/bin/python3):
    from dq_claim import record_claim
    record_claim(conn, runner="kis_supply_extend_history",
                 table_name="foreign_institutional",
                 claimed_rows=got_from_source, persisted_rows=written,
                 note="stocks=85 fail=0")
"""
from __future__ import annotations

import os
import re
import sys

CLAIM_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS dq_runner_claim (
    id          BIGSERIAL PRIMARY KEY,
    run_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    runner      TEXT        NOT NULL,
    table_name  TEXT        NOT NULL,
    claimed_rows BIGINT     NOT NULL,
    note        TEXT
)
"""


def record_claim(conn, runner, table_name, claimed_rows, persisted_rows=None,
                 source_rows=None, note=None):
    """러너 1회 실행의 자기신고를 기록한다.

    claimed_rows  : 로더가 **저장했다고 보고한** 행수(upsert 포함 — 재수집 시 중복도 센다)
    persisted_rows: 실제 테이블 행수 **델타**(신규 행만). claimed 와 다른 것이 정상일 수 있다.
    source_rows   : 소스 API 가 준 행수. **이 값이 있어야 파서 실패를 오탐 없이 잡는다**:
                    ``source > 0 AND claimed == 0`` = API 는 줬는데 로더가 아무것도 저장 안 함
                    (2026-09-24 파서 키 불일치 유형).
    생략 시 **NULL** 로 기록된다(claimed 로 대체되지 않는다). 특히 ``source_rows`` 를 생략하면
    ``parse_failure`` 판정이 항상 0 이 되어 그 러너의 파서 실패 알림이 **절대 울리지 않는다**
    — 실측 2026-09-25: extend_history 가 정확히 이 상태였고, 위임 리뷰가 잡아냈다. 호출부는
    ``src_total > 0 and written == 0`` 을 판정해 **exit 4** 로 신호한다.
    """
    cur = conn.cursor()
    try:
        cur.execute(CLAIM_TABLE_DDL)
        cur.execute(
            "ALTER TABLE dq_runner_claim ADD COLUMN IF NOT EXISTS persisted_rows BIGINT"
        )
        cur.execute(
            "ALTER TABLE dq_runner_claim ADD COLUMN IF NOT EXISTS source_rows BIGINT"
        )
        cur.execute(
            """INSERT INTO dq_runner_claim
                   (runner, table_name, claimed_rows, persisted_rows, source_rows, note)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (runner, table_name, int(claimed_rows),
             None if persisted_rows is None else int(persisted_rows),
             None if source_rows is None else int(source_rows), note),
        )
        conn.commit()
    finally:
        cur.close()


# 2026-09-25: 종전의 ``_table_count`` / ``verify_claims`` 를 **삭제**했다.
# 이유(위임 리뷰 지적, repo 전체 grep 으로 확인): ``verify_claims`` 는 **어디서도 호출되지
# 않는 죽은 코드**였고, 그 docstring 이 "개수가 어긋나면 러너는 exit code 를 0 이 아니게 해야
# 한다"는 **강제되지 않는 계약**을 선언해 감지가 되는 것처럼 보이게 했다. 실제 검증은 각 러너가
# `_record_claim(s)` 안에서 직접 하고(소스 수신량 vs 저장량), 명확한 실패는 **exit 4** 로
# 신호한다. 계약을 코드와 일치시키려면 죽은 선언을 남기지 않는 편이 낫다.


# ---------------------------------------------------------------------------
# 2026-10-01 (R23): **크론 직행 러너용** 자기신고 배선 — 러너 내부에서 부른다.
# WHY: 크론 래퍼(run_with_claim) 방식은 크론 파일 수정이라 사람 승인 항목이었다. 러너가 직접
# (source/claimed/persisted) 3값을 남기면 크론 파일을 건드리지 않고 같은 보증을 얻는다.
# 실측 2026-09-29: 크론이 직접 돌리는 수집 러너 7개(9개 경로)가 자기신고 0건이었다.
#
# 설계 원칙(사고 반복 금지):
#   · **자기신고 실패가 수집을 깨면 안 된다** — 모든 함수가 예외를 삼키고 stderr 로만 알린다.
#   · 자기신고 커넥션은 **autocommit + 단명** — 장수명 트랜잭션이 DDL 과 교착한 실측 사고
#     (2026-09-28 run_with_claim) 재발 방지.
#   · `claimed` 는 **파서가 만들어낸 행수**다(적재 시도도 실제 삽입도 아니다). 재실행 창에서
#     inserted=0 이 정상이므로 그 값을 claimed 로 넣으면 `dq_claim_parse_failure` 오탐이 난다.
#   · 아무 일도 안 한 실행(API 호출 0회)은 남기지 않는다 — 빈 실행은 자기신고 대상이 아니다.
#
# 사용(러너 내부, 3줄):
#     claim_start("krx_daily", "market_data")
#     ... 수집 ...
#     claim_finish("krx_daily", source_rows=recv, claimed_rows=parsed)

_TABLE_RE = re.compile(r"^[a-z_][a-z0-9_]*$")
_CLAIMS: dict = {}


def _conn_target():
    """자기신고용 DB 좌표.

    저장소 .env 의 **컨테이너 좌표**(POSTGRES_HOST=postgres / 5432)가 호스트 프로세스에 물려
    있으면 이름해석 실패로 죽는다 → 컨테이너 안(/.dockerenv)이 아니면 127.0.0.1:호스트포트로
    되돌린다(호스트 포트는 POSTGRES_HOST_PORT, 기본 5434 = docker port stock_postgres).
    """
    host = os.environ.get("POSTGRES_HOST", "127.0.0.1") or "127.0.0.1"
    port = int(os.environ.get("POSTGRES_PORT", "5434") or 5434)
    if host in ("postgres", "db") and not os.path.exists("/.dockerenv"):
        host = "127.0.0.1"
        port = int(os.environ.get("POSTGRES_HOST_PORT", "5434") or 5434)
    return host, port


def _open_conn():
    import psycopg2  # 지연 import — 드라이버 없는 환경(점검 모드)에서도 모듈 import 는 성공해야 한다

    host, port = _conn_target()
    conn = psycopg2.connect(
        host=host, port=port,
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
        connect_timeout=10,
    )
    conn.autocommit = True
    return conn


def _count(conn, table: str) -> int:
    if not _TABLE_RE.match(table):
        raise ValueError("테이블명 거부: %r" % (table,))
    cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM " + table)  # noqa: S608 - _TABLE_RE 로 검증됨
        return int(cur.fetchone()[0])
    finally:
        cur.close()


def claim_start(runner: str, table_name: str, note: str = "") -> bool:
    """실행 전 테이블 행수 스냅샷(persisted 델타의 기준). 실패해도 예외를 올리지 않는다."""
    try:
        conn = _open_conn()
        try:
            before = _count(conn, table_name)
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 - 수집을 깨지 않는다
        _CLAIMS.pop(runner, None)
        print("[claim] %s: 시작 스냅샷 실패(%s: %s) — 자기신고 생략"
              % (runner, type(e).__name__, e), file=sys.stderr)
        return False
    _CLAIMS[runner] = {"table": table_name, "before": before, "note": note}
    return True


def claim_finish(runner: str, claimed_rows=None, source_rows=None,
                 persisted_rows=None, note=None) -> bool:
    """실행 후 자기신고 기록. `persisted_rows` 를 주지 않으면 테이블 행수 델타로 계산한다.

    claimed_rows 를 생략하면 source_rows 로 채운다(= '파서가 소스만큼 만들었다'는 보수적 가정 —
    오탐 parse_failure 를 만들지 않는 방향). 실패해도 예외를 올리지 않는다.
    """
    rec = _CLAIMS.pop(runner, None)
    if rec is None:
        return False
    try:
        table = rec["table"]
        persisted = persisted_rows
        if persisted is None:
            conn = _open_conn()
            try:
                persisted = _count(conn, table) - rec["before"]
            finally:
                conn.close()
        claimed = claimed_rows if claimed_rows is not None else source_rows
        if claimed is None:
            claimed = 0
        notes = [n for n in (rec.get("note"), note) if n]
        notes.append("persisted=%s(%d->%d)" % (
            "delta" if persisted_rows is None else "explicit",
            rec["before"], rec["before"] + (persisted or 0)))
        conn = _open_conn()
        try:
            record_claim(conn, runner, table, claimed_rows=claimed,
                         persisted_rows=persisted, source_rows=source_rows,
                         note=" ".join(notes))
        finally:
            conn.close()
        print("[claim] %s -> %s source=%s claimed=%s persisted=%s"
              % (runner, table, source_rows, claimed, persisted), flush=True)
        return True
    except Exception as e:  # noqa: BLE001 - 수집을 깨지 않는다
        print("[claim] %s: 자기신고 실패(%s: %s)" % (runner, type(e).__name__, e),
              file=sys.stderr)
        return False
