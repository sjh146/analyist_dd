"""paper_channel_check.py — 페이퍼 트레이딩 채널 검증.

전략 시그널이 paper:factor_signals로만 발행되고 trade:signals(실매매)에는
절대 가지 않는지 확인한다. 오늘 날짜가 리밸런싱 게이트가 아니면 analyze()가
빈 신호를 주므로, 게이트에 정렬된 과거 리밸런싱 날짜(2026-06-28)로 직접 검증.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.storage.postgres_storage import PostgresStorage
from app.storage.redis_storage import RedisStorage
from app.strategies.value_strategy import ValueStrategy

storage = PostgresStorage()
redis = RedisStorage()
rd = "2026-06-28"  # 게이트 정렬 날짜 (backtest에서 신호 확인된 날)

strat = ValueStrategy(storage)
signals = strat.analyze(asof_date=rd)
print(f"ValueStrategy @ {rd}: {len(signals)} signals")
print("  actions:", sorted({s['action'] for s in signals}))

def _slen(redis, stream):
    """스트림 길이 (RedisStreams에는 xlen 없음 — raw client로)."""
    try:
        return redis._client.xlen(stream)
    except Exception:
        return 0

before_paper = _slen(redis, "paper:factor_signals")
before_trade = _slen(redis, "trade:signals")
n_pub = 0
for s in signals[:5]:
    ok = redis.publish_paper_signal(s)
    n_pub += 1 if ok else 0
after_paper = _slen(redis, "paper:factor_signals")
after_trade = _slen(redis, "trade:signals")
print(f"paper:factor_signals: {before_paper} -> {after_paper} (발행 {n_pub})")
print(f"trade:signals: {before_trade} -> {after_trade} (실매매 — 변화 0이어야 함)")
print("RESULT:", "PASS" if after_paper > before_paper and after_trade == before_trade else "FAIL")
