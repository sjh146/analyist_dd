"""
ackman_strategy - AckmanStrategy (빌 애크먼식 장기 테제원장 전략).

테제원장(thesis ledger) 소비 전략:
- 활성 테제(position_theses, status='active')를 DB 직접 조회
- 판정 원장(thesis_verdicts, verdict_date DESC) 소비 → 4종 매도 시그널
  1) 파기 즉시 / 2) 손상 2연속 → 파기 승격 / 3) MoS 달성(가격 ≥ intrinsic×0.95)
  4) 보유 2년 초과(created_at 기준 730일)
- 미보유 + 매도 조건 없는 활성 테제 → 진입(buy) 시그널 + 포지션 사이징

paper_only 전략 — 신호 생성만 담당, 발행은 main.py 계층(전용 페이퍼 스트림)이 처리.
point-in-time 판정이라 look-ahead bias가 없다.
"""

import logging
from datetime import date, datetime
from typing import Dict, List, Optional

from app.strategies.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)

STRATEGY_NAME = "ackman_fundamental"
VERDICT_BROKEN = "파기"
VERDICT_DAMAGED = "손상"
DAMAGE_PROMOTION_COUNT = 2      # 손상 2회 연속 → 파기 승격 (PLAN §5.3)
MOS_EXIT_THRESHOLD = 0.95       # 가격 ≥ intrinsic × 0.95 → '테제 달성' 매도 (PLAN §5.3)
MAX_HOLDING_DAYS = 730          # 최대 보유 2년 (PLAN §5.3)
POSITION_SIZE_PCT_DEFAULT = 0.15  # 종목당 10~20% (PLAN §5.2), 기본 15%
BUY_CONFIDENCE = 0.75           # 진입 신뢰도 (position_theses에 ackman_score 미저장 — 가정 12)
CONFIDENCE_BY_REASON = {        # 매도 사유별 신뢰도
    "파기": 1.0, "손상_2연속_승격": 0.9, "MoS_달성": 0.9, "보유_2년_초과": 0.7,
}
EXIT_REASON_PRIORITY = ("파기", "손상_2연속_승격", "MoS_달성", "보유_2년_초과")


def _as_date(value) -> date:
    """date/datetime/str → date 변환.

    datetime은 `.date()`, 이미 date면 그대로, str은 `date.fromisoformat`.
    파싱 불가(str 형식 오류) 시 ValueError를 그대로 던져 — 테제별 try/except가
    해당 테제를 fail-open으로 건너뛰게 한다. (None은 호출 전에 거른다.)
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def position_quantity(entry_price, equity, position_size_pct: float) -> int:
    """순수 포지션 사이징 (테스트 대상).

    entry_price/equity가 None·<=0 또는 pct<=0 → 0(포지션 불가),
    그 외 → max(1, int(equity * pct / price)).
    """
    try:
        price = float(entry_price)
        eq = float(equity)
        pct = float(position_size_pct)
    except (TypeError, ValueError):
        return 0
    if price <= 0 or eq <= 0 or pct <= 0:
        return 0
    return max(1, int(eq * pct / price))


class AckmanStrategy(BaseStrategy):
    """테제원장 장기 전략 — 판정 소비 + 4종 매도 + 진입/사이징."""

    def __init__(self, storage, config: Optional[Dict] = None):
        super().__init__(STRATEGY_NAME, storage, config)
        self.position_size_pct = float(self.config.get("position_size_pct", POSITION_SIZE_PCT_DEFAULT))

    def analyze(self, asof_date=None) -> List[Dict]:
        """활성 테제 → 판정 소비 → 매도/진입 시그널 생성."""
        # 1) asof 정규화: None→date.today(), str→date.fromisoformat (FactorStrategyBase 관례)
        if asof_date is None:
            asof = date.today()
        else:
            asof = _as_date(asof_date)

        # 2) 활성 테제 조회 (fail-open: 예외/빈 값 → [])
        try:
            theses = self.storage.get_active_theses(STRATEGY_NAME) or []
        except Exception as e:
            logger.warning(f"get_active_theses 실패 — 빈 테제로 처리: {e}")
            theses = []

        # 3) 보유 종목 집합 (fail-open)
        try:
            held = {p["stock_code"] for p in (self.storage.get_positions() or [])}
        except Exception as e:
            logger.warning(f"get_positions 실패 — 미보유로 처리: {e}")
            held = set()

        # 4) 테제별 try/except fail-open — 테제 1건 실패가 사이클 전체를 죽이지 않음
        signals = []
        for thesis in theses:
            try:
                verdicts = self.storage.get_thesis_verdicts(
                    thesis["id"], limit=DAMAGE_PROMOTION_COUNT
                ) or []
                reason = self._exit_reason(thesis, verdicts, asof)
                if reason is not None:
                    # 매도 조건 충족 → 보유 중일 때만 sell (미보유면 매도·매수 모두 억제)
                    if thesis["stock_code"] in held:
                        signals.append({
                            "action": "sell",
                            "stock_code": thesis["stock_code"],
                            "price": 0,
                            "reason": f"{STRATEGY_NAME}: 테제 청산 — {reason}",
                            "strategy_name": STRATEGY_NAME,
                            "confidence": CONFIDENCE_BY_REASON[reason],
                            "thesis_id": thesis["id"],
                        })
                elif thesis["stock_code"] not in held:
                    # 매도 조건 없음 + 미보유 → 진입(buy)
                    signals.append({
                        "action": "buy",
                        "stock_code": thesis["stock_code"],
                        "price": 0,
                        "reason": f"{STRATEGY_NAME}: 테제 승인 진입 (thesis {thesis['id']})",
                        "strategy_name": STRATEGY_NAME,
                        "confidence": BUY_CONFIDENCE,
                        "thesis_id": thesis["id"],
                        "position_size_pct": self.position_size_pct,
                    })
            except Exception as e:
                logger.warning(f"테제 {thesis.get('id')} 처리 실패 — 스킵(fail-open): {e}")
                continue

        # 5) 시그널 반환
        return signals

    def _exit_reason(self, thesis: Dict, verdicts: List[Dict], asof: date) -> Optional[str]:
        """EXIT_REASON_PRIORITY 순서로 첫 매칭 매도 사유 반환, 없으면 None.

        get_latest_price(MoS) 호출을 제외하고는 순수 판정(추가 DB 접근 없음).
        """
        verdicts = verdicts or []

        # 1) 파기 즉시
        if verdicts and verdicts[0]["verdict"] == VERDICT_BROKEN:
            return "파기"

        # 2) 손상 2회 연속 → 파기 승격 (verdicts는 verdict_date DESC — 최근 2건 모두 손상 = 연속)
        if (
            len(verdicts) >= 2
            and verdicts[0]["verdict"] == VERDICT_DAMAGED
            and verdicts[1]["verdict"] == VERDICT_DAMAGED
        ):
            return "손상_2연속_승격"

        # 3) MoS 달성: 최신 종가 ≥ intrinsic × 0.95 (결측 → 스킵, fail-open)
        intrinsic = thesis.get("intrinsic_value")
        if intrinsic is not None:
            latest_close = self.storage.get_latest_price(thesis["stock_code"])
            if latest_close is not None and latest_close >= float(intrinsic) * MOS_EXIT_THRESHOLD:
                return "MoS_달성"

        # 4) 보유 2년 초과 (created_at None → 스킵)
        created_at = thesis.get("created_at")
        if created_at is not None:
            if (asof - _as_date(created_at)).days >= MAX_HOLDING_DAYS:
                return "보유_2년_초과"

        return None
