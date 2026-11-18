#!/usr/bin/env python3
"""protected_paths.py — 자율 코드 변경이 **절대 건드릴 수 없는 경로**의 단일 진실원.

WHY (2026-10-04): 자율 저작/위임 경로가 여러 개다(개선 오케스트레이터, 리서처 저작, ask_claude.sh).
목록을 각자 들고 있으면 한 곳만 빠져도 그 입구가 열린다 — 실제로 리서처 저작 경로에는 검사가 없었고
`authoring.target` 이 실주문 경로를 가리켜도 막는 코드가 없었다. 그래서 목록을 여기서 한 번만 정의하고
모든 입구가 이 모듈을 부른다(tests/test_protected_paths.py 가 오케스트레이터 목록과 일치를 강제한다).

규칙: 경로 문자열에 아래 토큰이 하나라도 들어가면 **사람 승인 대상**이다(needs_human).
"""
from __future__ import annotations

from typing import Optional

# 실주문 경로 · 정책 값 · 자격증명 · 킬스위치 · 파이프라인 본체 — 하나라도 닿으면 자동 병합/저작 금지.
FORBIDDEN = (
    "trader-agent",              # 실주문 (별도 리포)
    "full_pipeline_dd.sh",       # 야간 파이프라인 본체
    "champion_promote",          # 승격 게이트 본체
    "config/objective.json",     # 봉투·게이트 정책 값
    "objective.json",            # 위의 축약 표기
    "objective.py",              # 정책 읽기/쓰기 도구
    ".env",                      # 자격증명
    "kill_switch",               # 자동 중단 스위치
    "gate_promote_live_score",   # 라이브 스코어 게이트
)

# 사람이 직접 손대야 하는 이유(보고용) — 사유를 말하지 않고 거부만 하면 우회를 유발한다.
REASONS = {
    "trader-agent": "실주문 경로(계좌·주문) — 사람 승인 필요",
    "full_pipeline_dd.sh": "야간 파이프라인 본체 — 게이트·증거 생성 순서가 걸려 있다",
    "champion_promote": "챔피언 승격 게이트 본체 — 승격 권한은 게이트와 사람에게만",
    "config/objective.json": "봉투·게이트 정책 값 — 한도 변경은 사람 몫",
    "objective.json": "봉투·게이트 정책 값 — 한도 변경은 사람 몫",
    "objective.py": "정책 도구 — 값 변경 경로",
    ".env": "자격증명",
    "kill_switch": "자동 중단 스위치",
    "gate_promote_live_score": "라이브 스코어 게이트 — 승격 경로",
}


def violation(*texts: Optional[str]) -> Optional[str]:
    """주어진 문자열들 중 금지 토큰이 있으면 사유를, 없으면 None 을 돌려준다."""
    blob = " ".join(t for t in texts if t).lower()
    for token in FORBIDDEN:
        if token.lower() in blob:
            return "{0} → {1}".format(token, REASONS.get(token, "사람 승인 필요"))
    return None


def is_protected(*texts: Optional[str]) -> bool:
    return violation(*texts) is not None


if __name__ == "__main__":
    # 셸 경로(ask_claude.sh)에서 쓰는 CLI: 금지 대상이면 사유를 출력하고 rc=2.
    import sys
    texts = sys.argv[1:]
    reason = violation(*texts)
    if reason:
        print("금지 경로: {0}".format(reason), file=sys.stderr)
        sys.exit(2)
    sys.exit(0)
