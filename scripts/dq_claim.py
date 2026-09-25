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
