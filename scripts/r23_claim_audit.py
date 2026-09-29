#!/usr/bin/env python3
"""r23_claim_audit — 자기신고(record_claim)가 없는 '크론 직행' 수집 러너 재고조사 (R23, 읽기 전용).

WHY (2026-09-29 리서처 실측): 자기신고가 없으면 '조용한 0행 적재'를 잡을 수 없다 —
2026-09-24 유형(파서 키 불일치로 +0행을 적재하고도 exit 0)이 진행파일에 '완료'로 남았던 사고가
그것이다. 자기신고(소스/저장/신규 3값)를 남기면 `dq_claim_parse_failure`(= source>0 AND
claimed==0)가 그 유형을 잡고, `dq_runner_claim` 대조가 매 틱 돈다.

그런데 dq_runner_claim 을 실제로 남기는 러너는 4개뿐이다(48h 실측: kis_supply_backfill ·
kis_supply_extend_history · build_supply_market_features · build_macro_features).
**크론이 직접 돌리는 수집 러너는 전부 자기신고가 0건**(grep 실측) → 이 경로에서 0행이 적재돼도
DQ 메트릭에는 아무 흔적이 없다.

판정: 미자기신고 러너 수 → `check_target` {"op": "<=", "value": 0}.
적용(다음 사이클): 스크립트 내부에서 `dq_claim.record_claim` 을 부르거나(권장 — 크론 파일 수정 불필요)
크론 래퍼에서 `scripts/run_with_claim.py --runner <이름> -- python3 <러너>` 로 감싼다
(후자는 **크론 파일 수정 = 사람 승인 항목**).
"""
from __future__ import annotations

import os
import re
import sys

PROJ = "/home/jhshi/analyist_dd"

# (설명, 크론 래퍼, 실제 수집 러너) — 크론이 직접 돌리는 DB 쓰기 경로만.
TARGETS = [
    ("일봉(KRX OpenAPI)", "/home/jhshi/cron/daily_bars.sh", "scripts/krx_daily.py"),
    ("KIS 일봉(폴백)", "/home/jhshi/cron/daily_bars.sh", "services/kis-collector/kis_app/main.py"),
    ("KIS 일봉(정규)", "/home/jhshi/cron/kis_daily.sh", "services/kis-collector/kis_app/main.py"),
    ("KIS 분봉", "/home/jhshi/cron/kis_minute.sh", "services/kis-collector/kis_app/main.py"),
    ("KRX 지수·파생", "/home/jhshi/cron/krx_offline.sh", "scripts/krx_derivatives_collect.py"),
    ("KIS 공매도", "/home/jhshi/cron/kis_short_program.sh", "scripts/kis_short_selling_backfill.py"),
    ("KIS 프로그램매매", "/home/jhshi/cron/kis_short_program.sh", "scripts/kis_program_trading_collect.py"),
    ("거시 ECOS/FRED", "/home/jhshi/cron/macro_daily.sh", "scripts/macro_backfill.py"),
    ("데이터 갭 백필", "/home/jhshi/cron/data_gap_backfill.sh", "scripts/data_gap.py"),
]

MARKERS = ("record_claim", "run_with_claim", "dq_claim")


def has_claim(path: str) -> bool:
    """러너 소스에 자기신고 흔적이 있는가. 파일이 없으면 None(판정 불가 → 누락 아님으로 계상 X)."""
    full = os.path.join(PROJ, path)
    try:
        with open(full, encoding="utf-8") as f:
            src = f.read()
    except OSError:
        return None
    return any(m in src for m in MARKERS)


def main() -> int:
    missing, present, unknown = [], [], []
    for label, cron, runner in TARGETS:
        ok = has_claim(runner)
        item = f"{label} ({runner})"
        if ok is None:
            unknown.append(item)
        elif ok:
            present.append(item)
        else:
            missing.append(item)

    # 같은 러너가 여러 크론 경로에서 불릴 수 있다(kis_app.main = 일봉/분봉/백필) →
    # 판정 수치는 **고유 러너 파일 수**로 센다(경로 중복으로 부풀리지 않는다).
    uniq = sorted({it.split("(")[-1].rstrip(")") for it in missing})
    print(f"[자기신고] 있음 {len(present)}개 경로 · 누락 {len(uniq)}개 러너"
          f"({len(missing)}개 경로) · 판정불가 {len(unknown)}개")
    for it in missing:
        print(f"  ✗ 누락 경로: {it}")
    for it in unknown:
        print(f"  ? 판정불가: {it}")
    print("적용 경로: 러너 내부 record_claim(권장 — 크론 파일 수정 불필요)"
          " 또는 크론 래퍼 run_with_claim(크론 수정=사람 승인 항목)")
    print(len(uniq))
    return 0 if not uniq else 2


if __name__ == "__main__":
    sys.exit(main())
