#!/usr/bin/env python3
"""news_event_backfill — 죽어 있는 뉴스 이벤트/테마 피처 살리기 (규칙 기반 추출).

배경(실측): news_event_extraction / news_events 가 0행이라 news_event_features 의
event_*_5d 18개와 theme_exposure_5d 가 전부 상수 0이었다. 원인은 죽은 추출기 —
scripts/news_revive_pipeline.py 백필이 감성/진위만 저장하고 event_type/themes/
importance 를 저장하지 않았다. 이 스크립트가 그 구멍을 규칙 기반(한국어 키워드
사전)으로 메운다.

택소노미/스키마는 LLM 분석기와 동일한 것을 재사용한다:
  app.models.schemas.EVENT_TAXONOMY / TIME_RANGE_TAXONOMY / StructuredNews
  (services/news-analyzer/app/models/schemas.py)
저장 SQL은 기존 writer(app/storage/postgres_storage.py:save_event_extraction)와
동일한 컬럼 집합에 created_at 을 **명시**해 쓴다(= 기사 발행시각). raw_json 에는
백필 표식/provenance 를 추가로 남긴다.
유료 LLM/외부 API 호출은 하지 않는다(규칙 기반 전용).

시간 정합(time alignment) — 2026-09-24 수정:
  백필 v1 은 created_at 을 테이블 기본값(now())으로 넣어 5,753행이 전부 '오늘'
  버킷에 몰렸다. 그 결과 ① 피처 리더의 "최근 N일" 윈도우가 시간이 지나면 0으로
  돌아가고 ② 같은 종목의 서로 다른 날짜(2026-09-16 vs 2026-09-23) 피처가 완전히
  동일하게 나와 학습 시 미래 정보 누수(look-ahead)/라벨 무관 피처가 됐다.
  수정: created_at = news_analysis.published_at (없으면 analyzed_at) 로 재적재한다.
  news_events 의 event_date / time_bucket / first_article_at / last_article_at 는
  clusterer 가 extraction.created_at 에서 파생하므로 함께 발행시각 기준이 된다.

실행(컨테이너 stock_news_analyzer, cwd=/app — scripts/ 가 /app/scripts 로 마운트됨):

    # 0) 사전 측정 (현재 상수 0 확인)
    docker exec stock_news_analyzer python scripts/news_event_backfill.py --stage verify

    # 1) 규칙 기반 추출 → news_event_extraction INSERT (중복 article_id+event_type 스킵)
    #    --reset: 기존 백필 행(추출+클러스터)을 먼저 삭제 → 멱등 재적재
    docker exec stock_news_analyzer python scripts/news_event_backfill.py --stage extract --dry-run
    docker exec stock_news_analyzer python scripts/news_event_backfill.py --stage extract --reset

    # 2) news_event_extraction → news_events 클러스터링 (기존 clusterer/저장 함수 1회 호출)
    #    --cluster-window-hours 0 → 윈도우 없이 전체(발행시각이 과거로 흔어진 백필 필수)
    docker exec stock_news_analyzer python scripts/news_event_backfill.py --stage cluster --cluster-window-hours 0

    # 3) 피처 검증 (stock_xgboost_ml 컨테이너에서 — 피처 리더 실제 호출)
    docker exec stock_xgboost_ml python /app/scripts/news_event_backfill.py --stage verify
    docker exec stock_xgboost_ml python /app/scripts/news_event_backfill.py --stage verify-time

안전:
- 읽기/쓰기 대상은 news_event_extraction, news_events 2개 테이블뿐.
- 컨테이너 재시작/중지 없음, 모델 학습/임베딩 없음(CPU 경합 회피).
- 중복 삽입 방지: (article_id, event_type) 이 이미 있으면 건너뛴다.
- stock_code 는 stocks 테이블에 존재하는 코드만 넣는다.
- 되돌리기: python scripts/news_event_backfill.py --stage revert  (백필 행만 삭제)
"""

import argparse
import html
import json
import logging
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("news_event_backfill")

# 백필로 생성된 행 표식 (되돌리기·검증용)
BACKFILL_MARK = "rule_backfill_v1"

# RSS 본문(HTML) 정리용 — 태그/URL 을 먼저 제거하지 않으면 URL 내부 문자열
# (예: google.com/rss/articles/CBMi... 의 'CB')이 키워드로 오탐된다(실측).
_TAG_RE = re.compile(r"<[^>]+>")
_URL_RE = re.compile(r"https?://\S+")
_WS_RE = re.compile(r"\s+")


def clean_text(text):
    """RSS HTML 본문 → 태그/URL 제거한 평문."""
    if not text:
        return ""
    t = html.unescape(str(text))
    t = _TAG_RE.sub(" ", t)
    t = _URL_RE.sub(" ", t)
    return _WS_RE.sub(" ", t).strip()


_KW_CACHE = {}


# 키워드 오탐 배제 사전: 한국어는 조사/합성어 경계가 없어 부분일치가 뭉개진다.
# 실측: '화재' → '문화재'(289건), '붕괴' → '신뢰 붕괴', '폭발' → '수요 폭발'.
EVENT_EXCLUDE = {
    "화재": ["문화재", "화재보험", "화재 예방", "화재예방", "화재감시", "화재훈련"],
    "폭발": ["폭발적", "수요 폭발", "실적 폭발", "매출 폭발", "성장 폭발", "관심 폭발"],
    "붕괴": ["신뢰 붕괴", "주가 붕괴", "장벽 붕괴", "벽 붕괴", "국경 붕괴", "특허장벽"],
    "지진": ["지진계", "지진파"],
    "홍수": ["홍수 출하", "홍수처럼", "홍수주의"],
    "감자": ["감자 가격", "감자 수확", "감자칩", "감자탕", "감자 농사"],
    "소각": ["소각장", "소각로", "소각 시설"],
    "인사": ["인사동", "인사말", "인사이드", "인사이트", "인사청문", "인사 평가"],
    "정전": ["정전기", "정전기적"],
    "매각": ["매각 불발"],
}


def _search_ok(text, rx, kw):
    """배제 사전을 통과하는 키워드 매치가 존재하는지."""
    excl = EVENT_EXCLUDE.get(kw)
    if not excl:
        return rx.search(text) is not None
    for m in rx.finditer(text):
        ctx = text[max(0, m.start() - 6):m.end() + 6]
        if not any(x in ctx for x in excl):
            return True
    return False


def kw_hit(kw, text_title, text_body):
    """키워드 매칭 (오탐 억제).

    - ASCII(영문 약어: CB, BW, M&A, MOU, AI …)는 대소문자 구분 + 단어 경계 필수.
      → URL/영단어 내부 부분일치('CBMi') 오탐 차단.
    - 한글 키워드는 부분일치(조사 결합 대응), 단 EVENT_EXCLUDE 로 합성어 오탐 배제.
    """
    rx = _KW_CACHE.get(kw)
    if rx is None:
        if kw.isascii():
            rx = re.compile(r"(?<![A-Za-z0-9])" + re.escape(kw) + r"(?![A-Za-z0-9])")
        else:
            rx = re.compile(re.escape(kw))
        _KW_CACHE[kw] = rx
    if _search_ok(text_title, rx, kw):
        return "title"
    if _search_ok(text_body, rx, kw):
        return "body"
    return None

# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------
def _db_conn():
    import psycopg2

    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )


# ---------------------------------------------------------------------------
# 종목 매칭 (scripts/news_revive_pipeline.py 의 match_stock 과 동일 규칙)
# ---------------------------------------------------------------------------
UNIVERSE_SQL_CURATED = """
SELECT md.stock_code, s.stock_name
FROM market_data md
JOIN stocks s ON md.stock_code = s.stock_code
WHERE s.market = 'KOSDAQ' AND md.trade_date >= '2026-04-01'
GROUP BY md.stock_code, s.stock_name
HAVING COUNT(*) >= 50
ORDER BY md.stock_code
LIMIT %s
"""

UNIVERSE_SQL_TURNOVER = """
WITH recent AS (
    SELECT stock_code, SUM(trading_value) AS tv
    FROM market_data
    WHERE trade_date >= (SELECT max(trade_date) - 20 FROM market_data)
    GROUP BY stock_code
)
SELECT r.stock_code, s.stock_name, s.market
FROM recent r
JOIN stocks s ON r.stock_code = s.stock_code
ORDER BY r.tv DESC
LIMIT %s
"""


def load_universe(pg, limit_curated=50, limit_turnover=200):
    """뉴스 수집과 동일한 유니버스 [(code, name), ...]."""
    out = {}
    cur = pg.cursor()
    cur.execute(UNIVERSE_SQL_CURATED, (limit_curated,))
    for code, name in cur.fetchall():
        out[code] = (code, name)
    cur.execute(UNIVERSE_SQL_TURNOVER, (limit_turnover,))
    for code, name, _market in cur.fetchall():
        if code not in out:
            out[code] = (code, name)
    cur.close()
    return list(out.values())


def match_stock(text, stocks_sorted):
    """텍스트에 포함된 종목명 중 가장 긴 것(우선) 반환. 없으면 None.

    scripts/news_revive_pipeline.py:match_stock 과 동일한 규칙(가장 긴 이름 우선).
    """
    if not text:
        return None
    best = None
    for code, name in stocks_sorted:
        if name in text:
            if best is None or len(name) > len(best[1]):
                best = (code, name)
    return best


# ---------------------------------------------------------------------------
# 규칙 기반 이벤트 추출 (EVENT_TAXONOMY 화이트리스트에 1:1 대응)
# ---------------------------------------------------------------------------
# (키워드, 가중치). 제목에 걸리면 가중치 ×2.
EVENT_RULES = {
    "실적발표": [
        ("실적", 2.0), ("영업이익", 2.0), ("영업손실", 2.0), ("매출액", 1.5),
        ("순이익", 1.5), ("흑자전환", 2.5), ("적자전환", 2.5), ("어닝", 2.0),
        ("잠정실적", 2.0), ("실적발표", 2.0), ("컨센서스", 1.5), ("가이던스", 1.5),
        ("분기 실적", 2.0), ("매출", 0.8), ("영업이익률", 1.5),
    ],
    "배당": [
        ("배당", 2.5), ("배당금", 2.0), ("주당배당", 2.0), ("중간배당", 2.5),
        ("결산배당", 2.5), ("무배당", 2.0), ("주주환원", 1.5), ("배당성향", 1.5),
    ],
    "유상증자·감자": [
        ("유상증자", 3.0), ("무상증자", 2.5), ("제3자배정", 3.0), ("감자", 1.5),
        ("주식병합", 2.5), ("주식분할", 2.0), ("액면병합", 2.5), ("증자", 2.0),
        ("납입자본금", 1.5),
    ],
    "CB·BW": [
        ("전환사채", 3.0), ("신주인수권", 3.0), ("신주인수권부사채", 3.0),
        ("교환사채", 2.5), ("사채", 1.5), ("CB", 2.0), ("BW", 2.0),
        ("전환우선주", 2.0), ("메자닌", 2.0),
    ],
    "M&A": [
        ("합병", 2.5), ("인수합병", 3.0), ("M&A", 3.0), ("경영권", 2.0),
        ("인수", 1.5), ("매각", 1.5), ("우선협상대상자", 2.5), ("스팩합병", 3.0),
        ("최대주주 변경", 2.0), ("지분 인수", 2.0), ("주식 양수", 2.0),
    ],
    "지분변동": [
        ("지분", 1.5), ("최대주주", 2.0), ("대량보유", 2.0), ("5%룰", 2.0),
        ("소유 주식 수량", 2.5), ("지분율", 2.0), ("주식 취득", 1.5),
        ("주식 처분", 1.5), ("보유 지분", 2.0), ("특수관계인", 1.5),
    ],
    "수주": [
        ("수주", 2.5), ("공급계약", 3.0), ("계약 체결", 2.5), ("납품", 2.0),
        ("단일판매", 2.5), ("수주잔고", 2.0), ("도급", 1.5), ("공급 확대", 2.0),
        ("수주 계약", 2.5), ("턴키", 1.5), ("발주", 1.5),
    ],
    "신제품": [
        ("신제품", 2.5), ("출시", 2.0), ("신약", 2.5), ("임상", 2.0),
        ("허가", 1.5), ("승인", 1.5), ("론칭", 2.0), ("상용화", 2.0),
        ("품목허가", 2.5), ("바이오시밀러", 2.0), ("기술 개발", 1.5),
        ("개발 완료", 1.5), ("新제품", 2.0),
    ],
    "특허": [
        ("특허", 2.5), ("특허 출원", 2.5), ("특허 등록", 2.5), ("상표권", 1.5),
        ("지식재산권", 1.5),
    ],
    "규제": [
        ("규제", 2.0), ("제재", 2.0), ("과징금", 2.5), ("행정처분", 2.5),
        ("불성실공시", 3.0), ("영업정지", 3.0), ("금지", 1.5), ("단속", 1.5),
        ("시정", 1.5), ("공정위", 2.0), ("금감원", 1.5), ("제동", 1.5),
        ("벌금", 1.5), ("조사 착수", 1.5), ("시정요구", 2.0),
    ],
    "소송": [
        ("소송", 2.5), ("기소", 2.5), ("고소", 2.5), ("판결", 2.0),
        ("법원", 1.5), ("압수수색", 2.0), ("검찰", 1.5), ("민사", 2.0),
        ("형사", 2.0), ("가처분", 2.0), ("불기소", 2.0),
    ],
    "부도·상폐·거래정지": [
        ("상장폐지", 3.0), ("상폐", 3.0), ("관리종목", 3.0), ("거래정지", 3.0),
        ("매매거래 정지", 3.0), ("부도", 3.0), ("파산", 3.0), ("회생", 2.5),
        ("퇴출", 2.5), ("투자경고", 2.5), ("투자주의", 2.0), ("시총 미달", 2.5),
        ("감사의견 거절", 3.0), ("상장적격성", 3.0),
    ],
    "리콜": [
        ("리콜", 3.0), ("회수 조치", 2.5), ("결함", 2.0), ("판매 중지", 2.5),
        ("판매중지", 2.5), ("사용 중지", 2.0),
    ],
    "자사주": [
        ("자사주", 2.5), ("자기주식", 2.5), ("주식 소각", 2.5), ("소각", 1.5),
        ("자사주 매입", 2.5), ("자사주 취득", 2.5),
    ],
    "임원변경": [
        ("대표이사", 2.5), ("사임", 2.0), ("선임", 1.5), ("임원", 1.5),
        ("인사", 1.0), ("CEO", 1.5), ("취임", 1.5), ("퇴진", 2.0),
        ("경질", 2.0), ("사외이사", 1.5), ("등기임원", 2.0),
    ],
    "파트너십": [
        ("협약", 2.0), ("MOU", 2.5), ("제휴", 2.0), ("파트너십", 2.5),
        ("업무협약", 2.0), ("협력", 1.5), ("공동개발", 2.0), ("전략적 제휴", 2.5),
        ("협업", 1.5),
    ],
    "거시경제": [
        ("금리", 1.5), ("기준금리", 2.0), ("환율", 1.5), ("물가", 1.5),
        ("소비자물가", 2.0), ("GDP", 2.0), ("연준", 1.5), ("한국은행", 1.5),
        ("관세", 1.5), ("인플레이션", 2.0), ("유가", 1.5), ("FOMC", 2.0),
        ("경기침체", 2.0), ("무역협상", 1.5),
    ],
    "시장지수·유동성": [
        ("코스피", 1.5), ("코스닥", 1.5), ("지수", 0.8), ("외국인 순매수", 2.0),
        ("기관 순매수", 1.5), ("공매도", 1.5), ("유동성", 1.5), ("수급", 0.8),
        ("장중", 0.8), ("마감", 0.8), ("급등", 0.8), ("급락", 0.8),
        ("시황", 1.5), ("강세", 0.7), ("약세", 0.7),
    ],
    "자연재해": [
        ("화재", 2.5), ("지진", 2.5), ("태풍", 2.5), ("홍수", 2.5),
        ("폭설", 2.5), ("폭우", 2.0), ("산불", 2.5), ("붕괴", 1.5),
        ("폭발", 1.5), ("침수", 2.0), ("정전", 2.0), ("한파", 1.5),
    ],
}

# 이벤트 타입별 중요도 바닥값 (시장 충격 크기, 0~1)
EVENT_BASE_IMPORTANCE = {
    "실적발표": 0.55, "배당": 0.40, "유상증자·감자": 0.50, "CB·BW": 0.45,
    "M&A": 0.60, "지분변동": 0.40, "수주": 0.45, "신제품": 0.40,
    "특허": 0.35, "규제": 0.50, "소송": 0.50, "부도·상폐·거래정지": 0.70,
    "리콜": 0.60, "자사주": 0.45, "임원변경": 0.35, "파트너십": 0.35,
    "거시경제": 0.40, "시장지수·유동성": 0.30, "자연재해": 0.55, "기타": 0.20,
}

# 이벤트 타입별 time_range (TIME_RANGE_TAXONOMY 화이트리스트)
EVENT_TIME_RANGE = {
    "실적발표": "1w", "배당": "1w", "유상증자·감자": "1m", "CB·BW": "1m",
    "M&A": "1m", "지분변동": "1m", "수주": "1m", "신제품": "1m",
    "특허": "1m", "규제": "1m", "소송": "1m", "부도·상폐·거래정지": "영구",
    "리콜": "1m", "자사주": "1w", "임원변경": "1w", "파트너십": "1m",
    "거시경제": "1m", "시장지수·유동성": "3d", "자연재해": "1m", "기타": "1w",
}

# 테마 사전 (themes JSONB). 걸린 테마가 없으면 event_type 을 테마로 쓴다
# (app/main.py:_write_news_graph 와 동일한 관례: taxonomy 기반 테마).
THEME_RULES = {
    "반도체": ["반도체", "HBM", "파운드리", "메모리", "웨이퍼", "소부장", "DDR", "낸드"],
    "디스플레이": ["디스플레이", "OLED", "LCD", "패널"],
    "2차전지": ["2차전지", "배터리", "양극재", "음극재", "전해질", "폐배터리", "리튬"],
    "AI": ["AI", "인공지능", "생성형", "LLM", "GPU", "엔비디아", "데이터센터"],
    "로봇": ["로봇", "로보틱스", "휴머노이드", "자동화"],
    "바이오": ["바이오", "제약", "신약", "임상", "백신", "항암", "바이오시밀러"],
    "방산": ["방산", "방위산업", "국방", "무기", "미사일"],
    "조선": ["조선", "선박", "LNG선", "해양플랜트"],
    "원전": ["원전", "원자력", "SMR", "핵"],
    "태양광": ["태양광", "태양전지", "솔라"],
    "풍력": ["풍력", "해상풍력"],
    "수소": ["수소", "연료전지", "수전해"],
    "전기차": ["전기차", "EV", "충전", "자율주행"],
    "우주항공": ["우주", "항공우주", "위성", "발사체", "누리호"],
    "게임": ["게임", "모바일게임", "신작"],
    "엔터": ["엔터", "K팝", "아이돌", "음원", "드라마", "콘텐츠"],
    "화장품": ["화장품", "뷰티", "코스메틱", "더마"],
    "식품": ["식품", "음료", "라면", "제과", "주류"],
    "화학": ["화학", "석유화학", "정유", "나프타"],
    "철강": ["철강", "제강", "열연", "후판"],
    "건설": ["건설", "부동산", "분양", "재건축", "토목"],
    "금융": ["금융", "은행", "증권", "보험", "저축은행", "여신"],
    "통신": ["통신", "5G", "6G", "통신사"],
    "유통": ["유통", "편의점", "백화점", "이커머스", "쇼핑"],
    "여행항공": ["여행", "항공", "호텔", "관광", "카지노"],
    "전력": ["전력", "송전", "변압기", "전선", "전력망", "에너지저장"],
    "사이버보안": ["보안", "해킹", "랜섬웨어", "정보보호"],
    "메타버스": ["메타버스", "XR", "NFT", "가상자산", "블록체인"],
    "농수산": ["농업", "수산", "사료", "축산", "종자"],
}

MAX_TYPES_PER_ARTICLE = 3
MIN_SCORE = 2.0


def extract_events(title, content, event_taxonomy):
    """기사 제목/본문 → [(event_type, importance, novelty, themes, time_range), ...].

    규칙 기반(한국어 키워드 사전). LLM 호출 없음.
    - 점수 = 매칭 키워드 가중치 합 (제목 매칭은 ×2)
    - 점수 >= MIN_SCORE 인 타입을 상위 MAX_TYPES_PER_ARTICLE 개까지 채택
    - 아무것도 안 걸리면 '기타' 1건
    """
    title = clean_text(title)
    body = clean_text(content)

    scored = []
    for etype, kws in EVENT_RULES.items():
        score = 0.0
        hits = 0
        title_hit = False
        for kw, weight in kws:
            where = kw_hit(kw, title, body)
            if where == "title":
                score += weight * 2.0
                hits += 1
                title_hit = True
            elif where == "body":
                score += weight
                hits += 1
        if score >= MIN_SCORE:
            scored.append((etype, score, hits, title_hit))

    scored.sort(key=lambda x: (-x[1], x[0]))
    if not scored:
        scored = [("기타", 0.0, 0, False)]
    else:
        top = scored[0][1]
        scored = [s for s in scored if s[1] >= max(MIN_SCORE, top * 0.5)]
        scored = scored[:MAX_TYPES_PER_ARTICLE]

    themes = []
    for theme, kws in THEME_RULES.items():
        if any(kw_hit(kw, title, body) for kw in kws):
            themes.append(theme)
            if len(themes) >= 5:
                break

    out = []
    for etype, score, hits, title_hit in scored:
        base = EVENT_BASE_IMPORTANCE.get(etype, 0.2)
        importance = base + 0.05 * min(score, 8.0) + (0.10 if title_hit else 0.0)
        importance = max(0.05, min(1.0, importance))
        novelty = 0.30 + 0.08 * hits + (0.20 if title_hit else 0.0)
        novelty = max(0.05, min(1.0, novelty))
        ev_themes = themes if themes else [etype]
        # 이벤트 타입 자체도 테마로 포함 (taxonomy 기반 테마 관례, 중복 제거)
        if etype not in ev_themes and len(ev_themes) < 5:
            ev_themes = ev_themes + [etype]
        out.append({
            "event_type": etype,
            "importance": round(importance, 4),
            "novelty": round(novelty, 4),
            "themes": [t[:50] for t in ev_themes][:5],
            "time_range": EVENT_TIME_RANGE.get(etype, "1w"),
            "score": round(score, 3),
            "title_hit": title_hit,
        })
    return out


def _bucket_hour(dt):
    """clusterer._time_bucket 과 동일한 2시간 버킷 시작시각."""
    return (dt.hour // 2) * 2


def backfill_cluster_prefixes(cur):
    """백필 추출행이 만들 수 있는 cluster_key 접두사 집합.

    clusterer 는 ``stock:event_type:date:HH-HH`` (+ 충돌 시 ``:seq``) 키를 만든다.
    seq 접미사가 붙은 클러스터까지 지우려면 접두사 매칭이 필요하다.
    """
    cur.execute(
        f"""
        SELECT DISTINCT stock_code, event_type, created_at
        FROM news_event_extraction
        WHERE raw_json->>'mark' = '{BACKFILL_MARK}'
        """
    )
    prefixes = set()
    for code, etype, ts in cur.fetchall():
        h = _bucket_hour(ts)
        prefixes.add(f"{code}:{etype}:{ts.date()}:{h:02d}-{h + 2:02d}")
    return prefixes


def reset_backfill(pg):
    """백필 행 삭제(추출 + 그로부터 만들어진 클러스터). 멱등 재적재용."""
    cur = pg.cursor()
    prefixes = backfill_cluster_prefixes(cur)
    n_events = 0
    if prefixes:
        cur.execute(
            "DELETE FROM news_events WHERE cluster_key LIKE ANY(%s)",
            ([p + "%" for p in prefixes],),
        )
        n_events = cur.rowcount
    cur.execute(f"DELETE FROM news_event_extraction WHERE raw_json->>'mark' = '{BACKFILL_MARK}'")
    n_ext = cur.rowcount
    pg.commit()
    cur.close()
    return n_events, n_ext


# ---------------------------------------------------------------------------
# stage: extract
# ---------------------------------------------------------------------------
def stage_extract(args):
    sys.path.insert(0, "/app")
    from app.models.schemas import EVENT_TAXONOMY, TIME_RANGE_TAXONOMY

    taxonomy = list(EVENT_TAXONOMY)
    for r in EVENT_RULES:
        assert r in taxonomy, f"rule type not in EVENT_TAXONOMY: {r}"
    for t in EVENT_TIME_RANGE.values():
        assert t in TIME_RANGE_TAXONOMY, f"bad time_range: {t}"

    pg = _db_conn()
    cur = pg.cursor()

    cur.execute("SELECT stock_code FROM stocks")
    valid_codes = {r[0] for r in cur.fetchall()}

    stocks = load_universe(pg, args.limit_curated, args.limit_turnover)
    stocks = [(c, n) for c, n in stocks if c in valid_codes]
    stocks_sorted = sorted(stocks, key=lambda x: -len(x[1]))
    logger.info("[extract] 유니버스 %d 종목 (stocks 검증 통과)", len(stocks_sorted))

    if args.reset:
        n_e, n_x = reset_backfill(pg)
        print(f"[extract] --reset: 기존 백필 news_events={n_e}행, "
              f"news_event_extraction={n_x}행 삭제")

    cur.execute(
        """
        SELECT id, title, content, published_at, analyzed_at, sentiment_score
        FROM news_analysis
        WHERE source = 'google_news'
        ORDER BY id
        """
    )
    articles = cur.fetchall()
    logger.info("[extract] 대상 기사 %d 건 (source='google_news')", len(articles))

    cur.execute("SELECT article_id, event_type FROM news_event_extraction")
    existing = {(r[0], r[1]) for r in cur.fetchall()}
    logger.info("[extract] 기존 추출 %d 행 (article_id+event_type 기준 중복 스킵)", len(existing))

    rows = []
    type_counter = Counter()
    stats = Counter()
    for aid, title, content, published_at, analyzed_at, sentiment in articles:
        if args.limit and len(rows) >= args.limit:
            break
        # 시간 정합: 기사 발행시각을 이벤트 시각으로 쓴다(없으면 분석시각 → now 폴백).
        event_time = published_at or analyzed_at or datetime.now()
        m = match_stock(title, stocks_sorted)
        match_field = "title"
        if m is None and args.match_content:
            m = match_stock((content or "")[:1000], stocks_sorted)
            match_field = "content"
        if m is None:
            stats["skip_no_stock"] += 1
            continue
        code, name = m
        if code not in valid_codes:
            stats["skip_invalid_code"] += 1
            continue
        stats[f"match_{match_field}"] += 1

        events = extract_events(title, content, taxonomy)
        for ev in events:
            if (aid, ev["event_type"]) in existing:
                stats["skip_dup"] += 1
                continue
            existing.add((aid, ev["event_type"]))
            core = f"{name} {ev['event_type']}: {(title or '').strip()}"[:200]
            raw = {
                "mark": BACKFILL_MARK,
                "stock_code": code,
                "stock_name": name,
                "event_type": ev["event_type"],
                "themes": ev["themes"],
                "sentiment_score": float(sentiment) if sentiment is not None else None,
                "importance": ev["importance"],
                "novelty": ev["novelty"],
                "time_range": ev["time_range"],
                "core_event_text": core,
                "published_at": published_at.isoformat() if published_at else None,
                "event_time": event_time.isoformat(),
                "match_field": match_field,
                "rule_score": ev["score"],
                "title_hit": ev["title_hit"],
                "extractor": BACKFILL_MARK,
            }
            rows.append((
                aid, code, ev["event_type"],
                json.dumps(ev["themes"], ensure_ascii=False),
                float(sentiment) if sentiment is not None else None,
                ev["importance"], ev["novelty"], ev["time_range"], core,
                json.dumps(raw, ensure_ascii=False),
                event_time,
            ))
            type_counter[ev["event_type"]] += 1
            stats["rows"] += 1

    print(f"[extract] 기사={len(articles)} 종목매칭(제목)={stats['match_title']} "
          f"종목매칭(본문)={stats['match_content']} 무매칭={stats['skip_no_stock']} "
          f"중복스킵={stats['skip_dup']} → 신규 추출행={len(rows)}")
    print("[extract] 이벤트 유형별 건수:")
    for etype, cnt in type_counter.most_common():
        print(f"    {etype}: {cnt}")

    if args.dry_run or not rows:
        print("[extract] DRY-RUN — DB 쓰기 없음")
        pg.close()
        return type_counter

    # 저장: 기존 writer(save_event_extraction)와 동일 컬럼 집합 + created_at 명시.
    # created_at = 기사 발행시각(published_at) → clusterer 가 이 값으로
    # event_date / time_bucket 을 만들므로 news_events 도 시간 정합해진다.
    from psycopg2.extras import execute_values

    insert_sql = """
        INSERT INTO news_event_extraction
            (article_id, stock_code, event_type, themes,
             sentiment_score, importance, novelty, time_range,
             core_event_text, raw_json, created_at)
        VALUES %s
    """
    if args.insert_mode == "storage":
        from app.storage.postgres_storage import PostgresStorage
        from app.models.schemas import StructuredNews

        st = PostgresStorage()
        for i, r in enumerate(rows, 1):
            structured = StructuredNews(
                stock_code=r[1], event_type=r[2],
                themes=json.loads(r[3]), sentiment_score=r[4] if r[4] is not None else 0.0,
                importance=r[5], novelty=r[6], time_range=r[7],
                core_event_text=r[8],
            )
            st.save_event_extraction(r[0], structured)
            if i % 500 == 0:
                print(f"    [storage] {i}/{len(rows)} 저장")
    else:
        for i in range(0, len(rows), args.batch_size):
            chunk = rows[i:i + args.batch_size]
            execute_values(cur, insert_sql, chunk)
            pg.commit()
            print(f"    [batch] {min(i + args.batch_size, len(rows))}/{len(rows)} 저장")
    cur.close()
    pg.close()

    # 시간 정합 보증(멱등): storage 모드(created_at 미지정 → now())로 들어간 행도
    # 발행시각으로 맞춘다. batch 모드는 이미 맞지만 재실행 안전성을 위해 동일 UPDATE.
    pg = _db_conn()
    cur = pg.cursor()
    cur.execute(
        f"""
        UPDATE news_event_extraction e
        SET created_at = COALESCE(na.published_at, na.analyzed_at, e.created_at)
        FROM news_analysis na
        WHERE e.article_id = na.id
          AND e.raw_json->>'mark' = '{BACKFILL_MARK}'
          AND e.created_at IS DISTINCT FROM COALESCE(na.published_at, na.analyzed_at)
        """
    )
    n_synced = cur.rowcount
    pg.commit()
    print(f"[extract] created_at 발행시각 정합 UPDATE = {n_synced}행")

    cur.execute(
        f"SELECT count(*) FROM news_event_extraction WHERE raw_json->>'mark' = '{BACKFILL_MARK}'"
    )
    print(f"[extract] 저장 완료 — 백필 표식 행 수 = {cur.fetchone()[0]}")
    cur.execute(
        f"""
        SELECT date_trunc('month', created_at)::date, count(*)
        FROM news_event_extraction
        WHERE raw_json->>'mark' = '{BACKFILL_MARK}'
        GROUP BY 1 ORDER BY 1
        """
    )
    print("[extract] created_at 월별 분포(발행시각 기준):")
    for d, c in cur.fetchall():
        print(f"    {d}: {c}")
    cur.close()
    pg.close()
    return type_counter


# ---------------------------------------------------------------------------
# stage: cluster (기존 clusterer + 기존 저장 함수 1회 호출)
# ---------------------------------------------------------------------------
def stage_cluster(args):
    sys.path.insert(0, "/app")
    from app.events.clusterer import cluster
    from app.storage.postgres_storage import PostgresStorage

    st = PostgresStorage()
    if args.cluster_window_hours > 0:
        since = datetime.now() - timedelta(hours=args.cluster_window_hours)
    else:
        # 윈도우 없이 전체 클러스터링. 백필 이벤트 시각은 발행시각 기준으로 과거에
        # 흔어져 있어(--reset 이후) 48h 윈도우로는 대부분 잡히지 않는다.
        pg = _db_conn()
        cur = pg.cursor()
        cur.execute(f"SELECT min(created_at) FROM news_event_extraction WHERE raw_json->>'mark' = '{BACKFILL_MARK}'")
        since = cur.fetchone()[0] or datetime(1970, 1, 1)
        cur.close()
        pg.close()
    rows = st.get_recent_event_extractions(since)
    logger.info("[cluster] 클러스터 대상 추출행 %d (since=%s)", len(rows), since)
    if not rows:
        print("[cluster] 대상 없음")
        return

    clusters = cluster(rows)
    print(f"[cluster] 추출행 {len(rows)} → 클러스터 {len(clusters)}")

    saved = 0
    for i, cl in enumerate(clusters, 1):
        st.save_event_cluster(cl)
        saved += 1
        if i % 500 == 0:
            print(f"    [cluster] {i}/{len(clusters)} upsert")

    types = Counter(cl.event_type for cl in clusters)
    print(f"[cluster] news_events upsert 완료 = {saved}")
    print("[cluster] 클러스터 event_type 별 건수:")
    for etype, cnt in types.most_common():
        print(f"    {etype}: {cnt}")

    # news_events 시각 컬럼 정합: 저장 함수(PostgresStorage.save_event_cluster)는
    # created_at 을 지정하지 않아 행 삽입시각(now())이 남는다. event_date /
    # first_article_at / last_article_at 은 clusterer 가 extraction.created_at
    # (= 기사 발행시각)에서 파생해 이미 맞지만, created_at 도 감사/모니터링에서
    # '이벤트 시각'으로 읽히므로 백필 클러스터에 한해 last_article_at 으로 맞춘다.
    pg = _db_conn()
    cur = pg.cursor()
    prefixes = backfill_cluster_prefixes(cur)
    n_synced = 0
    if prefixes:
        cur.execute(
            """
            UPDATE news_events SET created_at = last_article_at
            WHERE cluster_key LIKE ANY(%s)
              AND created_at IS DISTINCT FROM last_article_at
            """,
            ([p + "%" for p in prefixes],),
        )
        n_synced = cur.rowcount
    pg.commit()
    cur.execute("SELECT min(event_date), max(event_date) FROM news_events")
    span = cur.fetchone() or (None, None)
    pg.close()
    print(f"[cluster] news_events.created_at = last_article_at 정합 = {n_synced}행")
    print(f"[cluster] news_events.event_date 범위 = {span[0]} ~ {span[1]}")


# ---------------------------------------------------------------------------
# stage: verify (피처 리더 실제 호출 — stock_xgboost_ml 컨테이너에서 실행)
# ---------------------------------------------------------------------------
def stage_verify(args):
    sys.path.insert(0, "/app")
    from app.feature_engine.news_event_features import NewsEventFeatures

    pg = _db_conn()
    cur = pg.cursor()
    cur.execute("SELECT count(*) FROM news_event_extraction")
    n_ext = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM news_events")
    n_evt = cur.fetchone()[0]
    print(f"[verify] news_event_extraction={n_ext}행, news_events={n_evt}행")

    # 표본: 최근 5일 내 이벤트가 있는 종목 (없으면 추출행 상위 종목)
    cur.execute(
        """
        SELECT stock_code, event_date, count(*) AS cnt, max(event_type) AS sample_type
        FROM news_events
        WHERE event_date >= (now() - interval '5 days')::date
        GROUP BY stock_code, event_date
        ORDER BY cnt DESC
        LIMIT %s
        """,
        (args.sample,),
    )
    pairs = cur.fetchall()
    if not pairs:
        cur.execute(
            """
            SELECT stock_code, max(created_at)::date, count(*), max(event_type)
            FROM news_event_extraction
            GROUP BY stock_code
            ORDER BY count(*) DESC
            LIMIT %s
            """,
            (args.sample,),
        )
        pairs = cur.fetchall()

    reader = NewsEventFeatures()
    if args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
        pairs = []
        for c in codes:
            cur.execute("SELECT max(event_date) FROM news_events WHERE stock_code=%s", (c,))
            d = cur.fetchone()[0]
            pairs.append((c, d, 0, ""))
    cur.close()

    print(f"[verify] 표본 {len(pairs)}개 (stock_code, date) 쌍")
    nonzero = Counter()
    values = defaultdict(set)
    per_pair = []
    for code, d, cnt, sample_type in pairs:
        f = reader.get_all_features(code, pg)
        nz = {k: v for k, v in f.items() if v}
        for k in f:
            values[k].add(round(float(f[k]), 6))
            if f[k]:
                nonzero[k] += 1
        per_pair.append((code, d, cnt, sample_type, f))
        print(f"  ({code}, {d}) news_events건수={cnt} 대표이벤트={sample_type}")
        print(f"      event_* 비영: {json.dumps(nz, ensure_ascii=False)}")
        print(f"      market_impact_score={f.get('market_impact_score')} "
              f"theme_exposure_5d={f.get('theme_exposure_5d')}")

    all_feats = list(reader.get_all_features(pairs[0][0], pg).keys()) if pairs else []
    print(f"\n[verify] 피처별 집계 (표본 {len(pairs)}쌍 기준)")
    alive, constant = 0, 0
    for k in all_feats:
        vals = values[k]
        status = "비영" if nonzero[k] else "전부 0"
        if len(vals) > 1:
            status += "/비상수"
            alive += 1 if nonzero[k] else 0
        else:
            constant += 1
        print(f"  {k:28s} 비영표본={nonzero[k]:2d}/{len(pairs)}  서로다른값={len(vals):2d} "
              f"min={min(vals):.4f} max={max(vals):.4f}  [{status}]")
    print(f"\n[verify] 요약: news_event_features 피처 {len(all_feats)}개 중 "
          f"비영&비상수={alive}, 상수={constant}")
    pg.close()


# ---------------------------------------------------------------------------
# stage: verify-time (시간 정합 실측 — 같은 종목의 날짜별 피처 값이 달라지는가)
# ---------------------------------------------------------------------------
def stage_verify_time(args):
    """(종목, 날짜) 기준 실측 검증.

    - 피처 리더를 date 인자와 함께 직접 호출해 날짜별 event_*/theme_* 값을 뽑고
      서로 다른지(비상수) · 비영인지 판정한다.
    - date=None(레거시 now() 윈도우) 결과와 나란히 놓아 회귀/누수 여부를 보인다.
    """
    sys.path.insert(0, "/app")
    from app.feature_engine.news_event_features import NewsEventFeatures

    codes = [c.strip() for c in args.codes.split(",") if c.strip()] or ["000250"]
    dates = [d.strip() for d in args.verify_dates.split(",") if d.strip()]
    date_objs = [date.fromisoformat(d) for d in dates]

    pg = _db_conn()
    cur = pg.cursor()

    cur.execute("SELECT count(*) FROM news_event_extraction")
    n_ext = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM news_events")
    n_evt = cur.fetchone()[0]
    cur.execute("SELECT coalesce(sum(article_count), 0) FROM news_events")
    sum_art = cur.fetchone()[0]
    print(f"[verify-time] news_event_extraction={n_ext}행 / news_events={n_evt}행 / "
          f"sum(article_count)={sum_art} → {'일치' if sum_art == n_ext else '불일치'}")

    for tbl, col in (("news_event_extraction", "created_at"),
                     ("news_events", "event_date")):
        print(f"[verify-time] {tbl}.{col} 월별 분포:")
        cur.execute(
            f"SELECT date_trunc('month', {col})::date, count(*) "
            f"FROM {tbl} GROUP BY 1 ORDER BY 1"
        )
        for d, c in cur.fetchall():
            print(f"    {d}: {c}")

    reader = NewsEventFeatures()
    overall_diff = 0
    overall_total = 0
    for code in codes:
        cur.execute("SELECT count(*) FROM news_events WHERE stock_code = %s", (code,))
        n_ev_code = cur.fetchone()[0]
        print(f"\n[verify-time] 종목 {code} — news_events {n_ev_code}행, "
              f"비교 날짜 {dates}")
        if not n_ev_code:
            print("    (해당 종목 이벤트 없음 — 건너뜀)")
            continue

        cur.execute(
            """
            SELECT event_date, event_type, sum(article_count)
            FROM news_events WHERE stock_code = %s
            GROUP BY 1, 2 ORDER BY 1, 2
            """,
            (code,),
        )
        sample = cur.fetchall()
        span = f"{sample[0][0]}~{sample[-1][0]}" if sample else "-"
        print(f"    이벤트 스팬={span} (event_date,event_type,기사수) 표본 최대 8건:")
        for d, et, c in sample[:8]:
            print(f"      {d} {et} {c}")
        if len(sample) > 8:
            print(f"      ... 외 {len(sample) - 8}건")

        per_date = {d: reader.get_all_features(code, pg, d) for d in date_objs}
        legacy = reader.get_all_features(code, pg)  # date=None (하위호환 경로)
        names = list(per_date[date_objs[0]].keys())

        header = f"    {'feature':28s}" + "".join(f"{str(d):>13s}" for d in date_objs)
        header += f"{'date=None':>13s}  판정"
        print(header)
        n_diff = n_alive = 0
        for k in names:
            vals = [per_date[d][k] for d in date_objs]
            uniq = len({round(float(v), 6) for v in vals})
            distinct = uniq > 1
            nonzero = any(v for v in vals)
            if distinct:
                n_diff += 1
            if distinct and nonzero:
                n_alive += 1
            tag = "DIFF" if distinct else ("상수0" if not nonzero else "동일")
            row = f"    {k:28s}" + "".join(f"{float(v):13.4f}" for v in vals)
            row += f"{float(legacy[k]):13.4f}  {tag}"
            print(row)
        overall_diff += n_alive
        overall_total += len(names)
        print(f"    → 피처 {len(names)}개 중 날짜별로 값이 달라진 것={n_diff}, "
              f"비영&비상수={n_alive}")

    print(f"\n[verify-time] 요약: (종목,날짜) 그리드에서 비영&비상수 피처 "
          f"{overall_diff}/{overall_total}")
    print("[verify-time] 판정 기준: DIFF = 같은 종목인데 날짜별 event_* 값이 다름 "
          "(= 시간 정합 O), 상수0 = 그 윈도우에 이벤트 없음")
    cur.close()
    pg.close()


# ---------------------------------------------------------------------------
# stage: revert
# ---------------------------------------------------------------------------
def stage_revert(args):
    pg = _db_conn()
    cur = pg.cursor()
    prefixes = backfill_cluster_prefixes(cur)
    n_events = 0
    if prefixes:
        # 접두사 매칭 — clusterer 의 seq 접미사(':seq')가 붙은 클러스터도 함께 삭제.
        cur.execute(
            "DELETE FROM news_events WHERE cluster_key LIKE ANY(%s)",
            ([p + "%" for p in prefixes],),
        )
        n_events = cur.rowcount
    cur.execute(f"DELETE FROM news_event_extraction WHERE raw_json->>'mark' = '{BACKFILL_MARK}'")
    n_ext = cur.rowcount
    pg.commit()
    cur.close()
    pg.close()
    print(f"[revert] news_event_extraction {n_ext}행, news_events {n_events}행 삭제")


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="뉴스 이벤트/테마 피처 규칙 기반 백필")
    ap.add_argument("--stage", default="extract",
                    choices=["extract", "cluster", "verify", "verify-time", "revert", "all"])
    ap.add_argument("--limit", type=int, default=0, help="처리 기사 수 제한(0=전체)")
    ap.add_argument("--reset", action="store_true",
                    help="extract 전 기존 백필 행(추출+클러스터) 삭제 → 멱등 재적재")
    ap.add_argument("--limit-curated", type=int, default=50)
    ap.add_argument("--limit-turnover", type=int, default=200)
    ap.add_argument("--match-content", dest="match_content", action="store_true", default=True,
                    help="제목 매칭 실패 시 본문(앞 1000자) 폴백 매칭")
    ap.add_argument("--no-match-content", dest="match_content", action="store_false")
    ap.add_argument("--insert-mode", default="batch", choices=["batch", "storage"],
                    help="batch=기존 writer와 동일 컬럼의 배치 INSERT(기본, provenance 포함), "
                         "storage=기존 writer(save_event_extraction) 그대로 호출")
    ap.add_argument("--batch-size", type=int, default=500)
    ap.add_argument("--cluster-window-hours", type=int, default=48)
    ap.add_argument("--sample", type=int, default=8, help="verify 표본 (stock_code, date) 쌍 수")
    ap.add_argument("--codes", default="", help="verify/verify-time 대상 종목코드 CSV (지정 시 우선)")
    ap.add_argument("--verify-dates", dest="verify_dates",
                    default="2026-05-15,2026-07-15,2026-09-23",
                    help="verify-time 비교 날짜 CSV (ISO)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.stage == "extract":
        stage_extract(args)
    elif args.stage == "cluster":
        stage_cluster(args)
    elif args.stage == "verify":
        stage_verify(args)
    elif args.stage == "verify-time":
        stage_verify_time(args)
    elif args.stage == "revert":
        stage_revert(args)
    elif args.stage == "all":
        stage_extract(args)
        stage_cluster(args)


if __name__ == "__main__":
    main()
