#!/usr/bin/env python3
"""컨테이너 **내부** phase 가 찍은 '[claim] ...' 한 줄을 호스트가 대신 기록한다 (R28).

WHY (2026-10-02 실측, R28):
    저녁 파이프라인 Phase 1-1(yfinance 시세·기술지표)은 `docker exec -i stock_yfinance_collector
    sh -c 'cat > /tmp/phase_1_1.py'` 로 **컨테이너 안에서** 돈다. 그 컨테이너에는
    `scripts/dq_claim.py` 가 없으므로 러너 내부 배선(`claim_start`/`claim_finish`)이 원리적으로
    불가능하다. 그래서 phase 스크립트가 마지막에 기계가 읽는 한 줄

        [claim] <runner> <table> source=<n|-> claimed=<n|-> persisted=<n|->

    을 stdout 으로 찍고, 호스트 래퍼(`full_pipeline_dd.sh::run_docker_phase`)가 이 스크립트로
    파싱해 `dq_claim.record_claim` 으로 실제 행을 남긴다. R23/R27 이 '배선 문자열'이 아니라
    '실제 dq_runner_claim 행'으로 판정하므로, 이 배선이 있어야 yfinance 경로가 지표에 잡힌다.

설계 규칙(모두 실측 함정의 결과):
    · 자기신고 실패가 **수집을 깨면 안 된다** → 무슨 일이 있어도 exit 0, stderr 로만 알린다.
    · 커넥션은 `dq_claim._open_conn`(autocommit·단명)을 그대로 쓴다 — 장수명 트랜잭션 교착 재발 방지.
    · 여러 줄이 있으면 **마지막** [claim] 줄을 쓴다(총계 재신고 관례).
    · source/claimed/persisted 는 '-' 로 NULL 을 표현한다(값을 지어내지 않는다).

사용:
    /usr/bin/python3 scripts/yf_claim_from_phase_log.py <phase_log_path>
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# dq_claim 은 psycopg2 를 지연 import 하므로 모듈 import 자체는 드라이버 없이도 성공한다.
from dq_claim import _open_conn, record_claim  # noqa: E402

# [claim] runner table source=N claimed=N persisted=N   (N 은 정수 — persisted 는 음수 델타도 허용 — 또는 '-'=NULL)
_CLAIM_RE = re.compile(
    r"^\[claim\]\s+(\S+)\s+(\S+)\s+"
    r"source=(\d+|-)\s+claimed=(\d+|-)\s+persisted=(-?\d+|-)\s*$"
)


def _num(tok: str):
    return None if tok == "-" else int(tok)


def parse_claim_line(text: str):
    """텍스트에서 **마지막** '[claim] runner table source=.. claimed=.. persisted=..' 를 뽑는다.

    반환: dict(runner, table, source, claimed, persisted) 또는 None(해당 줄 없음).
    """
    found = None
    for ln in text.splitlines():
        m = _CLAIM_RE.match(ln.strip())
        if m:
            found = {
                "runner": m.group(1),
                "table": m.group(2),
                "source": _num(m.group(3)),
                "claimed": _num(m.group(4)),
                "persisted": _num(m.group(5)),
            }
    return found


def _load_dotenv_defaults():
    """호스트에서 손으로 돌릴 때를 대비해 repo `.env` 의 POSTGRES_* 를 **비어 있을 때만** 채운다.

    이유: 크론 래퍼(evening_pipeline.sh)는 이미 `set -a; . .env` 로 좌표를 물려주지만, 수동
    실행은 그렇지 않아 비밀번호가 비어 자기신고가 조용히 실패한다. 컨테이너 좌표(postgres:5432)가
    들어와도 `dq_claim._conn_target` 이 호스트 좌표(127.0.0.1:5434)로 되돌린다(실측 함정).
    """
    here = os.path.dirname(os.path.abspath(__file__))
    env_path = os.path.join(os.path.dirname(here), ".env")
    try:
        with open(env_path, encoding="utf-8", errors="replace") as f:
            for ln in f:
                ln = ln.strip()
                if not ln or ln.startswith("#") or "=" not in ln:
                    continue
                k, _, v = ln.partition("=")
                k = k.strip()
                if k.startswith("POSTGRES_") and k not in os.environ:
                    os.environ[k] = v.strip().strip('"').strip("'")
    except OSError:
        pass


def main(argv) -> int:
    path = argv[1] if len(argv) > 1 else ""
    if not path:
        print("[claim] 사용법: yf_claim_from_phase_log.py <phase_log_path>", file=sys.stderr)
        return 0  # 자기신고 실패는 수집을 깨지 않는다
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError as e:
        print(f"[claim] 로그 열기 실패({type(e).__name__}: {e}) — 자기신고 생략", file=sys.stderr)
        return 0

    try:
        rec = parse_claim_line(text)
    except Exception as e:  # noqa: BLE001
        print(f"[claim] 파싱 실패({type(e).__name__}: {e}) — 자기신고 생략", file=sys.stderr)
        return 0
    if not rec:
        return 0  # [claim] 줄이 없는 phase(다른 단계) — 정상 no-op

    try:
        _load_dotenv_defaults()
        conn = _open_conn()
        try:
            record_claim(
                conn, rec["runner"], rec["table"],
                claimed_rows=rec["claimed"],
                persisted_rows=rec["persisted"],
                source_rows=rec["source"],
                note="container-phase (R28 host-side)",
            )
        finally:
            conn.close()
        print(
            "[claim] host-recorded %s -> %s source=%s claimed=%s persisted=%s"
            % (rec["runner"], rec["table"], rec["source"], rec["claimed"], rec["persisted"]),
            flush=True,
        )
    except Exception as e:  # noqa: BLE001 - 자기신고 실패가 수집을 깨면 안 된다
        print(f"[claim] host 기록 실패({type(e).__name__}: {e}) — 수집엔 영향 없음", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
