#!/usr/bin/env python3
"""테제 온보딩 (Thesis Onboarding) — 테제원장(Thesis Ledger) M6 실가동 등록 파이프라인.

빌 애크먼식 장기 보유 테제를 신규 등록하는 승인 플로우. 스크리너가 선별한
매수 후보 CSV(data/reports/ackman_candidates_*.csv)를 읽어 AckmanScore 상위
후보에 테제 초안(4+1문장)을 생성하고, 승인 패키지(data/thesis/approvals)를
거쳐 사람 승인 후 position_theses에 INSERT한다 (docs/테제원장_PLAN.md §5,
.omo/plans/thesis-ledger-m6-live.md §결정본).

결정본 요약 (.omo/plans/thesis-ledger-m6-live.md §결정본):
- 후보 로딩: parse_candidates_csv / filter_candidates (순수 함수, DB·IO 0)
- 초안 생성: deepseek-v4-pro(high_stakes, THESIS_DRAFT_MODEL) — urllib, fail-open
- 승인 패키지: data/thesis/approvals/<approval_id>.json (pending→approved/rejected 단방향)
- 등록: position_theses INSERT (active, has_active_thesis 중복 방지)
- 알림: Discord 웹훅 (미설정 시 콘솔 폴백)

사용법:
  python3 scripts/thesis_onboarding.py --draft
  python3 scripts/thesis_onboarding.py --list
  python3 scripts/thesis_onboarding.py --approve <approval_id>

이 모듈은 todo 1 산출물(골격+상수+CSV 로딩/필터 순수 함수)까지 포함하며,
초안 생성기·승인 패키지·Discord·INSERT·CLI는 후속 todo에서 추가된다.
"""
import csv
import logging
import os
from typing import Dict, List

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("thesis_onboarding")

# ── PostgreSQL 접속 (ackman_screener 관례: env-driven) ──────────────────
PG_HOST = os.environ.get("POSTGRES_HOST", "postgres")
PG_PORT = int(os.environ.get("POSTGRES_PORT", 5432))
PG_DB = os.environ.get("POSTGRES_DB", "stock_trading")
PG_USER = os.environ.get("POSTGRES_USER", "stock_user")
PG_PASS = os.environ.get("POSTGRES_PASSWORD", "")

# ── 승인 패키지 / 후보 CSV 상수 (결정본 §thesis_onboarding) ──────────────
APPROVALS_DIR = "data/thesis/approvals"
DEFAULT_CSV_GLOB = "data/reports/ackman_candidates_*.csv"  # 최신 파일 선택 (타임스탬프 내림차순)
DEFAULT_TOP_N = 5
DEFAULT_MIN_SCORE = 0.01  # ackman_score = Q×V×C 승법 — 0이면 테제 성립 불가 (가정 A6)

# ── 초안 생성 모델 (P6 high_stakes, 가정 A2) ─────────────────────────────
DRAFT_MODEL = os.environ.get("THESIS_DRAFT_MODEL", "deepseek-v4-pro")
DRAFT_TEMPERATURE = 0.3
DRAFT_MAX_TOKENS = 8192  # colony-llm-gateway §4-1 high_stakes 행과 동일

# ── Discord 웹훅 / 전략명 ─────────────────────────────────────────────────
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")  # 미설정 → 콘솔/로그 폴백
THESIS_STRATEGY_NAME = "ackman_fundamental"

# ── 출력 CSV 컬럼 (스크리너 실CSV 헤더 그대로) ────────────────────────────
OUTPUT_COLUMNS = [
    "rank", "stock_code", "stock_name", "sector", "signal_date", "close_price",
    "ackman_score", "quality_score", "valuation_score", "catalyst_score", "reason",
]

# ── CSV 숫자 컬럼 (parse_candidates_csv에서 float 변환) ───────────────────
NUMERIC_COLUMNS = (
    "close_price", "ackman_score", "quality_score", "valuation_score", "catalyst_score",
)


def get_pg_conn():
    """psycopg2 연결 생성 (lazy import — 테스트 환경에 psycopg2 없어도 import 가능)."""
    import psycopg2
    return psycopg2.connect(
        host=PG_HOST, port=PG_PORT, dbname=PG_DB, user=PG_USER, password=PG_PASS
    )


def _to_float(v):
    """CSV 셀 → float 변환 (빈 값·변환 불가 → None, 0.0 대체 없음).

    변환 실패를 None으로 반환하는 사유: ackman_score를 0.0으로 뭉개면 결측과
    실제 0을 구분할 수 없고, filter_candidates의 min_score 필터가 None을
    안전하게 제외하기 때문 (ackman_screener._to_float 관례).
    """
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_candidates_csv(path):
    """후보 CSV(OUTPUT_COLUMNS 헤더) → dict 리스트 (순수 함수 — IO는 파일 읽기뿐).

    숫자 컬럼(NUMERIC_COLUMNS)은 float 변환, 변환 불가 → None.
    파일 없음/빈 파일/읽기 실패 → [] + logger.warning (예외 전파 금지).
    """
    if not path or not os.path.isfile(path):
        logger.warning(f"후보 CSV 없음: {path}")
        return []
    rows: List[Dict] = []
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            for raw in csv.DictReader(f):
                row = dict(raw)
                for col in NUMERIC_COLUMNS:
                    if col in row:
                        row[col] = _to_float(row[col])
                rows.append(row)
    except OSError as e:
        logger.warning(f"후보 CSV 읽기 실패 {path}: {e}")
        return []
    return rows


def filter_candidates(rows, top_n, min_score):
    """후보 dict 리스트 → ackman_score > min_score(strict) → 내림차순 → head(top_n).

    순수 함수 (DB·IO 0). ackman_score None/변환불가는 필터에서 제외(통과 안 시킴).
    rows 빈 리스트·top_n ≤ 0·인자 변환 불가 → [] (방어적 처리, 크래시 없음).
    """
    if not rows:
        return []
    try:
        top_n = int(top_n)
        min_score = float(min_score)
    except (TypeError, ValueError):
        return []
    if top_n <= 0:
        return []
    scored = []
    for row in rows:
        score = _to_float(row.get("ackman_score"))
        if score is None or score <= min_score:
            continue
        scored.append((score, row))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [row for _, row in scored[:top_n]]
