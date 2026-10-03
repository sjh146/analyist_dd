#!/usr/bin/env python3
"""R28 check — yfinance 컨테이너 phase 자기신고가 '실제 행'으로 남았는가 (주말-인지 판정).

WHY (2026-10-03 실측):
    종전 R28 check 는 `dq_runner_claim` 에서 `runner LIKE 'yfinance%' AND source_rows > 0`
    행을 최근 7일 창으로 세고 `>= 1` 을 요구했다. 그런데 저녁 파이프라인은 **평일 20:00 에만**
    돈다(`/etc/cron.d/analyist_dd`: `0 20 * * 1-5`). 배선 커밋(`2e96da1`, 2026-10-03 04:04) 직후
    첫 주말에 이 check 는 매 틱 0 을 반환해 '미달'을 기록했다 — **마땅히 판정 대상이 없는 날
    (주말·배선 전)을 결함으로 세는** 스킬이 경고한 '주말에 구조적으로 실패하는 check' 그 자체다.

판정 (게이트 = '배선 이후 실제로 기회가 있었는가'):
    · `reports/full_pipeline_dd_*.log` 중 배선일(>= DEPLOY_DATE) 이후 실행된 로그가 없으면
      → 판정 대상 없음 = 통과(1).  (주말·배선 전)
    · 그 로그가 `[claim] yfinance_market_data ... source=N>0` 줄을 담고 있으면
      → dq_runner_claim 에 대응 행이 있어야 통과. 로그엔 있는데 행이 0 이면 = 호스트 기록
        결함 → 0(미달). 행이 있으면 행수(N)를 그대로 출력(>= 1 이므로 충족).
    · 로그는 있는데 `[claim]`(source>0) 줄이 없으면 → phase 는 `n_recv > 0` 일 때만 [claim] 을
      찍으므로(휴장·신규데이터 없음 = 정상 no-op) 자기신고가 **기대되지 않는** 실행 = 통과(1).
      (빈 실행을 미달로 세면 오탐 — R27/자기신고 규칙과 동일 원칙.)
    · DB 조회가 불가하면 판정 불가를 통과로 두지 않는다(fail-closed = 0).

출력 계약: 표준출력의 **마지막 수치**가 판정값이다(`researcher_cycle.eval_check` 가 마지막 수를
읽는다). 사람이 읽을 사유는 전부 stderr 로 보낸다 — stdout 에 숫자를 여러 개 찍으면 오판한다.
"""
from __future__ import annotations

import glob
import os
import re
import subprocess
import sys
from datetime import date, datetime

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORTS = os.path.join(PROJ, "reports")

# 배선 커밋(2e96da1, 2026-10-03 04:04) 이전의 파이프라인 로그는 자기신고를 기대할 수 없다.
DEPLOY_DATE = date(2026, 10, 3)

_PIPELINE_RE = re.compile(r"full_pipeline_dd_(\d{8})_\d{4}\.log$")
_CLAIM_RE = re.compile(
    r"^\[claim\]\s+yfinance_market_data\s+\S+\s+source=(\d+)\b", re.MULTILINE
)


def eligible_pipeline_logs(reports_dir: str = REPORTS, deploy_date: date = DEPLOY_DATE):
    """배선일 이후(포함) 실행된 저녁 파이프라인 로그를 (실행일, 경로) 로 돌려준다(실행일 오름차순)."""
    out = []
    for p in glob.glob(os.path.join(reports_dir, "full_pipeline_dd_*.log")):
        m = _PIPELINE_RE.search(os.path.basename(p))
        if not m:
            continue
        try:
            d = datetime.strptime(m.group(1), "%Y%m%d").date()
        except ValueError:
            continue
        if d >= deploy_date:
            out.append((d, p))
    return sorted(out)


def any_claim_expected(logs) -> bool:
    """로그 중 하나라도 'source>0 인 yfinance [claim] 줄'을 담고 있으면 True(자기신고가 기대된 실행)."""
    for _d, p in logs:
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                txt = f.read()
        except OSError:
            continue
        for m in _CLAIM_RE.finditer(txt):
            if int(m.group(1)) > 0:
                return True
    return False


def count_claim_rows() -> "int | None":
    """dq_runner_claim 에서 yfinance 경로의 source>0 행 수(최근 7일). 조회 불가면 None(fail-closed)."""
    sql = (
        "SELECT COUNT(*) FROM dq_runner_claim "
        "WHERE runner LIKE 'yfinance%' AND run_at > now() - interval '7 days' "
        "AND source_rows > 0"
    )
    try:
        p = subprocess.run(
            ["docker", "exec", "stock_postgres", "psql", "-U", "stock_user",
             "-d", "stock_trading", "-tAc", sql],
            capture_output=True, text=True, timeout=60,
        )
    except Exception as e:  # noqa: BLE001 - 판정 실패가 틱을 죽이면 안 된다
        print(f"[r28] DB 조회 실패({type(e).__name__}: {e}) — fail-closed(0)", file=sys.stderr)
        return None
    txt = (p.stdout or "").strip()
    m = re.search(r"\d+", txt)
    if p.returncode != 0 or not m:
        print(f"[r28] DB 조회 비정상(rc={p.returncode}, out={txt[:80]!r}) — fail-closed(0)", file=sys.stderr)
        return None
    return int(m.group(0))


def decide(logs, claim_expected: bool, row_count) -> "tuple[int, str]":
    """순수 판정 — (stdout 판정값, stderr 사유). 부수효과 없음(회귀 테스트 대상)."""
    if not logs:
        return 1, (f"배선({DEPLOY_DATE}) 이후 완료된 저녁 파이프라인 로그 없음 "
                   f"(평일 20:00 전용) → 판정 대상 없음 → 통과")
    if not claim_expected:
        return 1, (f"배선 이후 파이프라인 {len(logs)}회 실행됐으나 yfinance [claim](source>0) 줄이 "
                   f"없다 → 자기신고가 기대되지 않는 실행(휴장·신규데이터 없음) → 통과")
    if row_count is None:
        return 0, "DB 조회 불가 → 판정 불가(fail-closed = 미달)"
    if row_count >= 1:
        return row_count, f"yfinance 자기신고 행 {row_count}건 ≥ 1 → 통과"
    return 0, ("로그엔 [claim](source>0)이 있는데 dq_runner_claim 에 yfinance 행 0건 "
               "→ 호스트 기록 결함 → 미달")


def main() -> int:
    logs = eligible_pipeline_logs(reports_dir=REPORTS, deploy_date=DEPLOY_DATE)
    expected = any_claim_expected(logs)
    rows = count_claim_rows() if expected else None
    value, reason = decide(logs, expected, rows)
    print(f"[r28] {reason}", file=sys.stderr)
    print(value)          # stdout 의 마지막 수치 = 판정값 (eval_check 계약)
    return 0


if __name__ == "__main__":
    sys.exit(main())
