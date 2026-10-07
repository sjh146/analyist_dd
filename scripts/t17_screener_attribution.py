#!/usr/bin/env python3
"""T17 check — 트레이더 환류(스크리너별 실현 성과)를 리서처가 소비할 수 있는가 (주말-인지 판정).

WHY (2026-10-07):
  T17 은 트레이더가 넘긴 환류 항목("[트레이더 환류] 스크리너별 실현 성과 — 어느 데이터가
  기여했는지 확인 필요")인데 command/check 가 비어 있어(=재현 명령 없음) 사이클이 소비하지
  못했다(핸드오프 채택률 0 의 한 사례). 이 프로브가 소비 지점을 만든다 — 발행(분석→트레이더)의
  반대편인 **환류(트레이더→분석)** 경로가 살아있는지, 그리고 **스크리너 귀속 라벨이 유실되지
  않았는지**를 매 틱 수치로 판정한다.

source of truth = `data/reports/trader_fill_stats.json`
  (`scripts/ingest_trader_fills.py` 산출 · jhshi crontab 평일 16:40 `trader_fills_ingest.sh`).

판정 (게이트 = '환류 경로가 살아있고 귀속이 측정 가능한가'):
  · 리포트 없음/파싱 불가        → 0(미달). 환류 경로가 끊겨 귀속을 측정할 수 없다.
  · `generated_at` 이 MAX_AGE_DAYS(5일)보다 오래됨 → 0(미달). 적재 크론이 죽었다.
    (5일 = 금 16:40 → 다음 주 월요일이 휴장인 경우의 최대 간격. 주말·연휴를 결함으로 세지 않는다 —
     '마땅히 판정 대상이 없는 날을 결함으로 세지 마라' 규칙.)
  · 닫힌 거래 n>0 인데 `by_screener` 가 비었거나 라벨 없는(빈 문자열) 키가 있음 → 0(미달). 귀속 붕괴.
  · 닫힌 거래 0건 → 판정 대상 없음(무거래는 결함이 아니다) → 1(통과).

※ 확률구간(ml_prob) 귀속 여부는 **정보 줄**로만 찍는다 — 그 필드를 채우는 주체는 트레이더
  (`tools/export_fills.py`)라 리서처가 고칠 수 없다. 여기서 미달로 세우면 '고쳐도 못 통과하는
  check' 가 된다(스킬 함정).

출력 계약: 표준출력의 **마지막 수치**가 판정값이다(`researcher_cycle.eval_check`). 사람이 읽을
귀속표·사유는 전부 stderr 로 보낸다 — stdout 에 숫자를 여러 개 찍으면 오판한다.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone

PROJ = os.environ.get("PROJ_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORT_PATH = os.path.join(PROJ, "data", "reports", "trader_fill_stats.json")

KST = timezone(timedelta(hours=9))
MAX_AGE_DAYS = 5  # 금 16:40 → 월 휴장 시 다음 화 16:40 ≈ 4일. 여유 1일.


def load_stats(path: str = REPORT_PATH):
    """환류 리포트를 읽는다. 없거나 깨졌으면 None(판정에서 미달로 처리)."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _parse_iso(s):
    if not isinstance(s, str):
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def decide(stats, now: datetime, max_age_days: int = MAX_AGE_DAYS):
    """순수 판정 — (stdout 판정값, stderr 사유). 부수효과 없음(회귀 테스트 대상)."""
    if not isinstance(stats, dict):
        return 0, "환류 리포트 없음/파싱 불가 → 스크리너 귀속 측정 불가 → 미달"
    dt = _parse_iso(stats.get("generated_at"))
    if dt is None:
        return 0, f"generated_at 파싱 불가({stats.get('generated_at')!r}) → 미달"
    if dt.tzinfo is None:  # naive 로 저장된 경우 KST 로 간주
        dt = dt.replace(tzinfo=KST)
    age = (now - dt).total_seconds() / 86400.0
    if age > max_age_days:
        return 0, f"환류 리포트가 {age:.1f}일 낡음(> {max_age_days}) → 적재 경로 정지 → 미달"

    rows = stats.get("rows")
    by = stats.get("by_screener") or {}
    if not isinstance(by, dict):
        by = {}
    rows_n = rows if isinstance(rows, int) else 0

    if rows_n > 0 and not by:
        return 0, f"닫힌 거래 {rows_n}건인데 by_screener 가 비어 있음 → 귀속 집계 결함 → 미달"
    bad = [k for k in by if not str(k or "").strip()]
    if bad:
        lost = sum(int((by[k] or {}).get("n", 0) or 0) for k in bad)
        return 0, f"스크리너 라벨 없는 키 {bad!r} → 귀속 불가 {lost}건 → 미달"
    if rows_n == 0:
        return 1, "닫힌 거래 0건 — 무거래는 결함이 아니다(환류 경로 정상) → 통과"
    return 1, (f"환류 리포트 신선({age:.2f}일) · 닫힌 거래 {rows_n}건 · "
               f"스크리너 {len(by)}종 귀속 유지 → 통과")


def attribution_lines(stats) -> "list[str]":
    """귀속표·모델확률 커버리지(정보용, stderr 로만)."""
    if not isinstance(stats, dict):
        return []
    out = [f"[t17] source={stats.get('source')} files={len(stats.get('files') or [])} "
           f"generated_at={stats.get('generated_at')}"]
    by = stats.get("by_screener") or {}
    for k, v in sorted(by.items(), key=lambda kv: -((kv[1] or {}).get("n", 0) or 0)):
        v = v or {}
        label = k if str(k or "").strip() else "(빈 라벨)"
        out.append(f"[t17]   screener={label!r} n={v.get('n')} "
                   f"net={v.get('net_pnl')}원 win={v.get('win_rate')}% "
                   f"기대값={v.get('expectancy_krw')}원 fees={v.get('fees')}")
    allv = stats.get("all") or {}
    scored = stats.get("scored_n")
    out.append(f"[t17]   전체 n={allv.get('n')} net={allv.get('net_pnl')}원 "
               f"기대값={allv.get('expectancy_krw')}원")
    out.append(f"[t17]   ★ 모델확률(ml_prob) 귀속 가능 거래 = {scored}건 "
               f"→ 확률구간별 기여도 측정 {'가능' if scored else '**불가**(트레이더 fills 에 ml_prob 미기록)'}")
    return out


def main() -> int:
    stats = load_stats()
    now = datetime.now(KST)
    for line in attribution_lines(stats):
        print(line, file=sys.stderr)
    value, reason = decide(stats, now)
    print(f"[t17] {reason}", file=sys.stderr)
    print(value)  # stdout 의 마지막 수치 = 판정값
    return 0


if __name__ == "__main__":
    sys.exit(main())
