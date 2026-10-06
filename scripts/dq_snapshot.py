#!/usr/bin/env python3
"""dq_snapshot — 데이터 수집·품질 스냅샷 (퀀트 리서처의 모니터링 심장부).

무엇을 하는가
  Prometheus 에서 DQ/수집 메트릭을 **직접 조회**해 ① 임계값 판정(ok/warn/breach)
  ② 추세 차트 PNG 생성 ③ 이력 JSONL 기록 을 한 번에 한다.

WHY Grafana 스크린샷이 아니라 직접 조회인가 (실측 2026-09-25)
  - Grafana 13.2.2 에는 grafana-image-renderer 플러그인이 **없다**(플러그인 51개 중 renderer 0개,
    /render 는 500 반환). 익명 접근도 401 로 막혀 있다.
  - 그래서 "그래프를 본다"를 자율적으로 하려면 렌더러 설치(컨테이너 변경·수백 MB)가 필요하다.
  - 대신 Prometheus HTTP API 는 **인증 없이** 열려 있고, 호스트에 matplotlib 이 있어
    같은 데이터를 같은 주기로 그릴 수 있다. 의존성이 없고 자율 실행이 가능하다.
  → 판정의 근거는 항상 **수치**이고, PNG 는 그 수치의 시각 자료다(이미지가 근거가 아니다).

사용
  /usr/bin/python3 scripts/dq_snapshot.py                 # 기본 7일 추세
  /usr/bin/python3 scripts/dq_snapshot.py --hours 24      # 최근 24시간
  /usr/bin/python3 scripts/dq_snapshot.py --no-chart      # 차트 없이 수치만
  종료코드: 0=정상, 2=경고, 3=위반
"""

import argparse
import glob
import json
import os
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from net_local import opener  # noqa: E402  (로컬 요청은 프록시 우회)
import urllib.request
from datetime import datetime, timedelta, timezone

import matplotlib
matplotlib.use("Agg")   # 헤드리스 — 디스플레이 없이 PNG 생성
import matplotlib.dates as mdates   # noqa: E402
import matplotlib.pyplot as plt     # noqa: E402
from matplotlib import font_manager  # noqa: E402

PROM = os.environ.get("PROM_URL", "http://127.0.0.1:9090")
PROJ = os.environ.get("RES_PROJ", "/home/jhshi/analyist_dd")
OUTDIR = os.path.join(PROJ, "data/reports/dq_snapshots")
HISTORY = os.path.join(OUTDIR, "history.jsonl")
KST = timezone(timedelta(hours=9))

# 한글 폰트가 없으면 라벨이 깨진다 → 있는 것만 골라 쓰고, 없으면 영문 라벨을 쓴다.
_KO = None
for _cand in ("NanumGothic", "Noto Sans CJK KR", "Malgun Gothic", "Noto Sans KR"):
    try:
        font_manager.findfont(_cand, fallback_to_default=False)
        _KO = _cand
        break
    except Exception:
        continue
if _KO:
    plt.rcParams["font.family"] = _KO
plt.rcParams["axes.unicode_minus"] = False

# (메트릭, 집계, warn, breach, 라벨)  ※ warn/breach=None 이면 정보용
SPECS = [
    # ⚠ 신선도는 **거래일** 기준으로 판정한다(달력일이 아니다).
    #   종전에는 달력일 고정 문턱 3/5 였는데 두 방향으로 틀렸다:
    #     · 과대(오탐) — 9/24~25 추석 휴장 + 주말이면 달력 3일이 **정상**인데 warn 이 뜨고,
    #       9/28(월) 00:00 틱은 달력 5일이 되어 **위반 오탐**이 예정돼 있었다(실측 2026-09-26 20:00 warn=3).
    #     · 과소(실명) — 같은 문턱은 **진짜 2거래일 적재 실패**를 warn 으로 숨긴다(3일까지 정상이므로).
    #   → 기준을 '휴장일을 제외한 거래일 지연'으로 바꾸고 문턱을 warn 1 / breach 2 로 **조인다**.
    #   휴장일 근거는 추정이 아니라 실측이다: data/krx_holidays.json 은 data_gap.py 가
    #   KIS 1종목 프로브가 no_data 를 반환한 날짜만 기록한 파일이다(2026-09-24·25 기록됨).
    #   파일을 못 읽으면(폴백) 종전 달력일 문턱 3/5 를 그대로 쓰고 그 사실을 보고에 남긴다.
    ("market_data_freshness_days", "max", 1.0, 2.0, "데이터 신선도(거래일)"),
    ("market_data_rows_recent", "sum", None, None, "최근 적재 행수"),
    ("market_data_zero_volume_ratio_20d", "max", 0.05, 0.10, "거래량0 비율"),
    ("market_data_frozen_ratio_20d", "max", 0.05, 0.15, "동결(가격 불변) 비율"),
    ("dq_asof_violation_rows", "sum", None, 0.0, "as-of 위반 행수"),
    ("dq_claim_parse_failure", "sum", None, 0.0, "러너 파서 실패"),
    # gap 은 **정보용(임계값 없음)** — 어떤 수치 문턱도 옳지 않다.
    # WHY (2026-09-25 실측 위반 오탐): gap = claimed(파서생성) - persisted(테이블 델타) 인데,
    #   멱등 upsert 러너가 이미 적재된 구간을 재실행하면 기존행이 ON CONFLICT 로 빠져
    #   gap 이 **적재량 규모로** 커진다(실측: claimed 117,155 / 신규 54,324 → gap 62,831 ≥ 5,000 breach).
    #   같은 실행이 실제로는 54,324행을 신규 적재한 **성공 실행**이었다.
    #   gap 을 문턱으로 잡으면 "재수집할수록 위반"이 되어 큰 백필이 항상 breach 로 뜬다.
    #   Prometheus 알림도 같은 이유로 gap 을 **알림하지 않는다**(config/prometheus/alert.dq.rules.yml NOTE 1).
    #   진짜 실패는 gap 이 아니라 source>0 AND claimed==0 = dq_claim_parse_failure 로 잡는다(그건 breach 0 유지).
    ("dq_claim_gap", "sum", None, None, "자기신고 갭(중복재수집 포함·정보용)"),
    ("dq_claim_source", "sum", None, None, "소스 수신 행수"),
    # ⚠ 임계값은 **기준선 위**에 둔다. 살아있는(nonzero_ratio>0) 피처만 분모로 세므로
    #    상수 피처는 정의상 여기 포함되고, 실측 기준선이 0.38(29/76)이다.
    #    warn 을 0.35 로 두면 0.38 >= 0.35 가 매 틱 성립해 **상시 경고**가 뜬다(실측 2026-09-25:
    #    값이 0.3816 으로 15스냅샷 내내 불변인데 warn). 문턱은 Prometheus 알림(>0.50)과 맞춘다.
    ("dq_feature_stock_constant_ratio", "max", 0.45, 0.50, "종목상수 피처 비율"),
    # ⚠ 착시/결측은 **개수(count)** 로 본다 — MAX 는 최악 피처 하나가 값을 지배해
    #   전체 테이블 기준 기준선이 0.9998 이다(R10 의 near-empty 피처). 실측 2026-09-25 20:20.
    #   임계값은 실측 기준선 **위**에 둔다: 착시>0.10 = 36개, 결측>0.90 = 8개.
    #   2026-09-25 21:17 기준선 이동: 15 → 36. 악화가 아니라 **측정 인구가 180 → 199개**로
    #   늘어난 결과다(R11 거시 2개 + R12 재무비율 19개 = +21). 같은 피처의 착시가 커진 게 아니다
    #   — 착시는 배치별로 R10 15 / R11 2 / R12 19 이고 09-24 패널 배치는 0개다(실측).
    #   인구가 늘면 개수 메트릭의 기준선도 함께 늘어난다 → 문턱도 그 위로 옮긴다.
    #   MAX 메트릭은 아래에 정보용으로 남겨 추세만 본다(임계값 없음).
    ("dq_feature_coverage_illusion_count", "max", 40.0, 45.0, "커버리지 착시 피처 수(기준선 36)"),
    ("dq_feature_null_ratio_high_count", "max", 10.0, 15.0, "결측90%↑ 피처 수(기준선 8)"),
    ("dq_feature_coverage_illusion_max", "max", None, None, "커버리지 착시 최대(정보용)"),
    ("dq_feature_null_ratio_max", "max", None, None, "피처 결측 최대(정보용)"),
    # ⚠ R14 배선(2026-10-06): 이 값은 **단조 증가**한다 — MIN(computed_at) 이 2026-09-24 18:11 에
    #   고정된 128행을 가리켜 정체가 진행 중이다(실측 이력: 0.89일@09-25 → 11.9일@10-06, +1일/일).
    #   증가 중인 값에는 기준선이 없어(스킬: "문턱을 잡기 전에 값이 변하는지 먼저 봐라") 임계값을
    #   주지 않는다 — 낮게 잡으면 해소 전까지 매 틱 발화해 무시되고(상시 발화 함정), 높게 잡으면 근거가 없다.
    #   대신 ALWAYS_REPORT 로 **매 틱 한 줄 보고**한다(정체 = 조용한 공전 금지). 문턱 신설은 리뷰보드 안건.
    ("dq_feature_oldest_age_days", "max", None, None, "가장 오래된 피처 측정 나이(일)"),
    ("dq_feature_market_level_count", "max", None, None, "시장레벨 피처 수"),
    ("dq_padding_rows_before_listing", "max", None, 0.0, "상장 전 행(padding)"),
    # 뉴스 분석 파이프라인 (앱은 30분 주기). 실측 2026-09-25: news_analysis 의 url 유니크 제약 누락으로
    # **2일간 저장이 전멸**(24h 3,657건 폐기)했는데 지표가 없어 로그 grep 전엔 아무도 몰랐다.
    # 신선도로 "돌지만 저장이 안 되는" 상태를 잡는다(2시간 넘게 새 저장 없으면 warn).
    ("news_analysis_freshness_hours", "max", 2.0, 6.0, "뉴스 저장 신선도(시간)"),
    ("news_analysis_freshness_rows_24h", "sum", None, None, "뉴스 24h 저장 행수"),
    ("feature_alive_count", "max", None, None, "살아있는 피처"),
    ("feature_dead_count", "max", None, None, "죽은 피처"),
    # ⚠ 북극성 보정(2026-09-28 신설): `feature_alive_count` 는 계약 #6(시장 전체 동일값 피처는
    #   횡단면 모델 피처로 제안 금지)을 위반한 피처도 '살아있다'로 센다 — R11 거시 16개가 그렇다
    #   (실측: cross_section_constant_ratio = 1.000, 살아 164 중 26개가 시장레벨). 그래서
    #   횡단면으로 실제 쓸 수 있는 수를 함께 본다(실측 138 = 164 − 26). 임계값은 두지 않는다
    #   (정보용 — 인구가 늘면 함께 늘어난다). 문턱이 필요해지면 실측 기준선 위에 잡아라.
    ("dq_feature_alive_xsec_count", "max", None, None, "횡단면 변별력 있는 살아있는 피처(시장레벨 제외)"),
]

# ── 상시 보고(정보용이지만 매 틱 보여야 하는 것) ──────────────────────────────
# 임계값 판정(ok/warn/breach)과 **별개**로, 정체처럼 '조용히 진행되는' 신호는 매 틱 한 줄로 올린다.
# 2026-10-05 리뷰보드 ③-7: dq_feature_oldest_age_days 는 지표엔 잡히는데 틱 보고에 한 번도 뜨지 않아
# R14(feature_coverage 09-24 고정 128행) 정체가 11일간 아무 틱에도 보고되지 않았다.
ALWAYS_REPORT = {
    "dq_feature_oldest_age_days": " — R14 feature_coverage 정체(09-24 고정 128행) 감시",
}

# 차트 라벨은 영문으로 쓴다 — 이 WSL 에는 한글 폰트가 없어 글리프가 전부 깨진다
# (실측 2026-09-25: "Glyph ... missing from current font" 경고가 라벨 수만큼 발생).
# 콘솔·JSON 은 한글 label 을 그대로 쓴다(사람이 읽는 쪽은 한글이 맞다).
# ── 모니터링 사각지대 기준선 (실측 2026-09-25) ────────────────────────────────
# 아래 15개는 **정상 가동 스냅샷 21개에서 21/21 전부 값이 있었다**(history.jsonl 실측).
# news_analysis_* 2개는 도중에 추가된 지표라 9/21 — 기준선에서 제외한다.
# 판정에 Prometheus 의 5분 lookback 을 이용한다: 스크랩 1회(60초) 누락으로는 nodata 가
# 생기지 않고, nodata = "5분 이상 연속 소실" = 진짜 장애다.
CORE_ALWAYS = {
    "market_data_freshness_days", "market_data_rows_recent",
    "market_data_zero_volume_ratio_20d", "market_data_frozen_ratio_20d",
    "dq_asof_violation_rows", "dq_claim_parse_failure", "dq_claim_gap",
    "dq_claim_source", "dq_feature_stock_constant_ratio",
    "dq_feature_coverage_illusion_max", "dq_feature_null_ratio_max",
    "dq_feature_market_level_count", "dq_padding_rows_before_listing",
    "feature_alive_count", "feature_dead_count",
}

EN = {
    "market_data_freshness_days": "data freshness (days)",
    "market_data_rows_recent": "rows ingested (recent)",
    "market_data_zero_volume_ratio_20d": "zero-volume ratio 20d",
    "market_data_frozen_ratio_20d": "frozen price ratio 20d",
    "dq_asof_violation_rows": "as-of violations (rows)",
    "dq_claim_parse_failure": "runner parse failures",
    "dq_claim_gap": "self-report gap",
    "dq_claim_source": "rows from source",
    "dq_feature_stock_constant_ratio": "stock-constant feature ratio",
    "dq_feature_coverage_illusion_max": "coverage illusion (max)",
    "dq_feature_null_ratio_max": "max feature null ratio",
    "dq_feature_market_level_count": "market-level features",
    "dq_padding_rows_before_listing": "pre-listing padded rows",
    "feature_alive_count": "alive features",
    "feature_dead_count": "dead features",
    "scrape_duration_postgres": "postgres scrape duration (s)",
}

# ── 수집 경로(스크랩) 건강 문턱 — 실측 2026-09-28 ────────────────────────────
# 정상 범위 실측 3.9~28.2초(scrape_interval 60s / scrape_timeout 45s), 타임아웃은 45.009초.
SCRAPE_JOB = "postgres"
SCRAPE_WARN_S = 30.0      # 정상 실측 최대(28.2초) 위에 둔다 — 기준선 아래에 두면 매 틱 경고.
SCRAPE_BREACH_S = 44.5    # scrape_timeout(45초) 도달 = 스크랩 실패 = dq_* 소실
SCRAPE_SERIES = 'scrape_duration_seconds{job="postgres"}'
SCRAPE_WINDOW = "6m"       # 실패가 '단발'인지 '지속'인지 가르는 창(스크랩 간격 60초 → 6~7 표본)
SCRAPE_FAILS_BREACH = 2    # 창 안 실패가 이 개수 이상이면 지속 장애(단발 = 재기동 과도기, 실측 ~26초)


HOLIDAY_PATH = os.path.join(PROJ, "data", "krx_holidays.json")


def load_holidays(path=HOLIDAY_PATH):
    """KRX 휴장일 집합 → (holidays: set[str], ok: bool).

    출처는 **실측**이다: data_gap.py 가 KIS 1종목 프로브가 no_data 를 반환한 날짜만
    이 파일에 기록한다(추정·달력 하드코딩이 아니다). 읽기 실패는 조용히 넘기지 않고
    ok=False 로 돌려주어 호출부가 폴백(달력일 문턱) 사실을 보고하게 한다.
    """
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return set(), False
    if isinstance(data, dict):
        data = data.get("holidays", [])
    if not isinstance(data, (list, tuple)):
        return set(), False
    out = set()
    for x in data:
        try:
            out.add(datetime.strptime(str(x), "%Y-%m-%d").strftime("%Y-%m-%d"))
        except ValueError:
            continue
    return out, True


def utc_date_of(now):
    """KST 시각 → 메트릭과 **같은 시계**의 날짜(UTC)."""
    return now.astimezone(timezone.utc).date()


# 전일 바가 '마땅히 있어야 할' 시각(KST). market_data 적재는 yfinance 수집기가 18:00 스윕을
# 끝내고 저장하는 **자정~새벽**에 일어난다(services/yfinance-collector/app/main.py: 스윕 전체를
# 모은 뒤 Step 6 에서 저장). 그래서 00:00 틱에 전일 바를 요구하면 적재가 진행 중일 때 오탐이 된다.
DUE_HOUR = 2


def trading_days_behind(freshness_days, today, holidays, now=None):
    """신선도(달력일) → **거래일 지연** = '마땅히 있어야 하는데 없는' 거래일 수.

    마지막 적재일 = (메트릭과 같은 시계의) 기준일 − freshness_days. 그 다음날부터
    **마지막으로 마감된 거래일**까지 평일(휴장 제외)을 센다.
    휴장·주말만 지났으면 0 → 정상. 실제 거래일을 하루 놓쳤으면 1 → warn, 이틀이면 2 → breach.

    실측 함정 두 가지를 함께 고친다(2026-09-28, WSL 재부팅 후 04:00 틱):
    ① **오늘 바는 아직 '마땅히 있어야 할' 것이 아니다.** 종전 구현은 `today` 를 그대로 세서
       장 시작도 안 한 04:00 에 지연 1(warn)을 띄웠다. 같은 이유로 정상 거래일이면 09:00 KST
       이후(UTC 날짜가 오늘로 넘어간 뒤) 16·18·20·22 시 틱이 **매일** warn 을 띄운다 —
       아무 결함이 없는데 발화하는 문턱은 문턱이 아니다(실측 DB: 9/23 이 마지막 거래일,
       9/24·25 휴장 + 주말 → 결번 0, 그런데 04:00 스냅샷은 warn 1).
    ② **메트릭의 시계는 UTC 다.** exporter 는 `CURRENT_DATE - MAX(trade_date)` 이므로
       KST 날짜에서 빼면 09:00 이전에 하루가 밀려 없는 결번을 만들고(①의 연료),
       진짜 결번은 하루 먹는다(금요일 바가 없는 월요일 → 종전 0 = 실명).
    """
    if freshness_days is None:
        return None
    raw = int(round(float(freshness_days)))
    ref = utc_date_of(now) if now is not None else today
    last = ref - timedelta(days=raw)
    back = 1 if (now is None or now.hour >= DUE_HOUR) else 2
    horizon = today - timedelta(days=back)
    n = 0
    d = last + timedelta(days=1)
    while d <= horizon:
        if d.weekday() < 5 and d.strftime("%Y-%m-%d") not in holidays:
            n += 1
        d += timedelta(days=1)
    return n


def _host_uptime_s():
    """호스트 업타임(초). 읽을 수 없으면 None — 판정은 '모름'으로 두고 위반을 유지한다."""
    try:
        with open("/proc/uptime", encoding="ascii") as f:
            return float(f.read().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def classify_blackout(nodata, n_total, core_missing, uptime_s):
    """조회 실패(nodata)를 위반/경고로 판정 → (severity, message). severity: 'breach' | 'warn'.

    기본은 **위반**이다 — nodata 를 판정에서 빼면 '모니터링 실명'이 '정상'으로 보고된다
    (실측 2026-09-25 20:01: postgres-exporter 스크랩 12.7초 > scrape_timeout 10초로 dq_* 51개가
    통째로 사라졌는데 rc=0 이었다).

    예외는 **기동 과도기**뿐이다(실측 2026-09-26 18:44: WSL 재부팅 18:44, 스냅샷 18:44:39,
    exporter 의 DB 커넥션 수립 18:44:44). 조건을 좁게 둔다 — ①전면 소실(≥90%)이고
    ②호스트 업타임이 300초 미만일 때만 경고로 낮춘다. 부분 소실(타겟 1개만 죽음)이나
    업타임이 지난 뒤의 소실은 그대로 위반이고, 다음 틱에서는 어떤 경우든 위반으로 재판정된다.
    """
    msg = (f"모니터링 사각지대: 핵심 {len(core_missing)}/{len(CORE_ALWAYS)}개 미조회 "
           f"(전체 nodata {len(nodata)}/{n_total}) — Prometheus 타겟/exporter 스크랩 확인")
    if nodata and len(nodata) / n_total >= 0.9 and uptime_s is not None and uptime_s < 300:
        return "warn", f"{msg} [기동 과도기: 호스트 업타임 {uptime_s:.0f}s — 다음 틱 재판정]"
    return "breach", msg


def _prune(outdir, keep_png=48, keep_json=48, keep_hist=2000):
    """보존 정책 — 스냅샷은 격 2시간마다 쌓인다(하루 12개, PNG ~200KB).

    정리하지 않으면 무한 증가한다(실측: 하루 12 PNG ≈ 2.4MB). 최근 48개(약 4일)만 남긴다.
    history.jsonl 은 요약 1줄씩이라 작지만 상한을 둬 장기적으로도 안전하게 만든다.
    """
    for ext, keep in ((".png", keep_png), (".json", keep_json)):
        files = sorted(glob.glob(os.path.join(outdir, "*" + ext)))
        for f in files[:-keep] if len(files) > keep else []:
            try:
                os.remove(f)
            except OSError:
                pass
    try:
        with open(HISTORY, encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) > keep_hist:
            with open(HISTORY, "w", encoding="utf-8") as f:
                f.writelines(lines[-keep_hist:])
    except OSError:
        pass


def _api(path, params):
    """Prometheus 조회. 도달 불가·응답 지연·JSON 파손이면 빈 dict(=값 없음)로 흘려보낸다.

    WHY: 예전에는 URLError 가 그대로 올라와 트레이스백과 함께 rc=1 로 죽었다(실측 2026-09-26:
    PROM_URL 을 닫힌 포트로 두면 `urllib.error.URLError: Connection refused` 스택만 남았다).
    사각지대를 판정하려고 만든 코드가 정작 사각지대에서 판정 줄 대신 스택을 출력한 것이다.
    조회 실패를 '값 없음'으로 넘기면 전 메트릭이 nodata 가 되어 위반/기동과도기로 **판정**된다.
    """
    url = f"{PROM}{path}?" + urllib.parse.urlencode(params)
    try:
        with opener(url).open(url, timeout=20) as r:  # 프록시 우회(net_local)
            return json.load(r)
    except (OSError, json.JSONDecodeError):
        return {}


def instant(name):
    """즉시 조회 → (대표값, 계열목록). 대표값은 aggregate(계열 중 max/sum)."""
    d = _api("/api/v1/query", {"query": name})
    res = d.get("data", {}).get("result", [])
    if not res:
        return None, []
    vals = []
    series = []
    for r in res:
        try:
            v = float(r["value"][1])
        except (KeyError, IndexError, ValueError):
            continue
        vals.append(v)
        series.append({"labels": {k: v2 for k, v2 in r["metric"].items() if k != "__name__"},
                       "value": v})
    return vals, series


def range_series(name, hours, step=1800):
    end = datetime.now(KST)
    start = end - timedelta(hours=hours)
    d = _api("/api/v1/query_range", {
        "query": name, "start": int(start.timestamp()),
        "end": int(end.timestamp()), "step": step})
    out = []
    for r in d.get("data", {}).get("result", []):
        pts = [(datetime.fromtimestamp(float(t), KST), float(v))
               for t, v in r["values"]]
        if pts:
            out.append({"labels": {k: v2 for k, v2 in r["metric"].items() if k != "__name__"},
                        "points": pts})
    return out


def classify_scrape_path(up, scrapes_6m, ok_6m, duration_s):
    """수집 경로(스크랩) 건강 판정 → (status, 문구|None). 조회 없는 순수 함수.

    신호 넷: 지금 스크랩 성공 여부(up) · 6분 창 스크랩 수 · 그중 성공 수 · 스크랩 소요(duration).
    · up=1, 지연이 문턱 아래 = ok (정상 실측 3.9~28.2초)
    · up=1, 지연이 임계 도달 = warn/breach — dq_* 가 소실되기 **전에** 알린다(리드타임)
    · up=0 인데 창 안 실패가 1회 = warn (exporter 재기동 직후 ~26초는 정상 과도기, 실측)
    · up=0 이고 실패 2회 이상 = breach (단발이 아니다 — dq_* 가 lookback 을 넘겨 소실된다)
    · up 을 못 읽었다 = nodata (없는 정보를 '정상'으로 세지 않는다)
    · up=0 인데 실패 횟수를 못 셌다 = breach (fail closed — 모르면 조용해지지 않는다)

    ⚠ 왜 '창 평균 0'이 아니라 '실패 횟수'인가 (실측 2026-09-28 18:16 KST):
      사고 당시 실측은 up=0 · avg_over_time(up[6m])=0.1667 이었다 — 6분 창에 **성공 스크랩이 아직
      1개 남아 있어** 평균이 0 이 아니었고, 평균 기준으로는 '단발 실패(과도기)'로 분류된다.
      6분 넘게 지속된 실명을 '재기동 과도기'라고 부르는 오독이다(그 시점 실패는 이미 5회).
      → 평균은 실패 *규모*를 뭉개므로, 총 스크랩 수와 성공 수를 따로 받아 실패 횟수로 가른다.
    """
    if up is None:
        return "nodata", f'수집 경로 신호 없음(up{{job="{SCRAPE_JOB}"}} 조회 불가) → 스크랩 건강 판정 불가'
    if up == 0:
        if scrapes_6m is None or ok_6m is None:
            return "breach", ("수집 모니터링 스크랩 실패(up=0) — 실패 횟수 확인 불가"
                              " → 모르면 조용해지지 않도록 위반으로 둔다")
        fails = int(round(scrapes_6m - ok_6m))
        if fails >= SCRAPE_FAILS_BREACH:
            tail = (f" — 스크랩 {duration_s:g}초 ≥ 타임아웃 {SCRAPE_BREACH_S:g}초" if duration_s is not None else "")
            return "breach", (f"수집 모니터링 스크랩 실패 지속(최근 {SCRAPE_WINDOW} 실패 {fails}회)"
                              + tail + " — dq_* 소실")
        return "warn", (f"수집 경로 스크랩 {fails}회 실패(재기동 과도기일 수 있음)"
                        f" — {SCRAPE_WINDOW} 내 {SCRAPE_FAILS_BREACH}회 이상이면 위반으로 올린다")
    if duration_s is not None and duration_s >= SCRAPE_BREACH_S:
        return "breach", f"스크랩 지연 {duration_s:g}초 ≥ {SCRAPE_BREACH_S:g}초(타임아웃 임계) — dq_* 소실 직전"
    if duration_s is not None and duration_s >= SCRAPE_WARN_S:
        return "warn", (f"스크랩 지연 {duration_s:g}초 ≥ {SCRAPE_WARN_S:g}초 — 타임아웃까지 여유 "
                        f"{SCRAPE_BREACH_S - duration_s:g}초")
    return "ok", None


def verdict(val, warn, breach):
    if val is None or (warn is None and breach is None):
        return "info"
    if breach is not None and val >= breach and (breach > 0 or val > 0 or breach == 0.0 and val > 0):
        if breach == 0.0:
            return "breach" if val > 0 else "ok"
        return "breach"
    if warn is not None and val >= warn:
        return "warn"
    return "ok"


def main():
    ap = argparse.ArgumentParser(description="DQ/수집 스냅샷")
    ap.add_argument("--hours", type=float, default=168.0, help="추세 조회 기간(시간)")
    ap.add_argument("--no-chart", action="store_true")
    a = ap.parse_args()

    now = datetime.now(KST)
    snap = {"ts": now.isoformat(timespec="seconds"), "hours": a.hours, "metrics": {}}
    breaches, warns, nodata = [], [], []

    holidays, hol_ok = load_holidays()
    snap["holiday_calendar"] = {"path": os.path.relpath(HOLIDAY_PATH, PROJ), "usable": hol_ok,
                               "n_holidays": len(holidays)}
    if not hol_ok:
        # 폴백 사실을 숨기지 않는다 — 거래일 판정이 꺼져 있으면 종전 달력일 문턱으로 판정된다.
        warns.append(f"휴장 캘린더 없음({snap['holiday_calendar']['path']}) → 신선도는 달력일 문턱 3/5 폴백")

    for name, agg, warn, breach, label in SPECS:
        vals, series = instant(name)
        if not vals:
            snap["metrics"][name] = {"label": label, "value": None, "status": "nodata"}
            nodata.append(name)
            continue
        val = max(vals) if agg == "max" else sum(vals)
        raw_days = None
        if name == "market_data_freshness_days":
            if hol_ok:
                raw_days = val                   # 달력일(원값)은 보존
                td = trading_days_behind(raw_days, now.date(), holidays, now=now)
                if td is not None:
                    val = float(td)
            else:
                # 폴백: 휴장 캘린더가 없으면 거래일 환산을 할 수 없다 → 종전 달력일 문턱 3/5.
                # (1/2 를 그대로 두면 주말마다 위반 오탐이 난다 — 판정을 바꿀 수 없는 상태에서는
                #  문턱도 같이 되돌리는 것이 맞다.)
                warn, breach = 3.0, 5.0
        st = verdict(val, warn, breach)
        snap["metrics"][name] = {"label": label, "value": val, "agg": agg, "status": st,
                                 "warn": warn, "breach": breach, "n_series": len(series),
                                 "series": series[:8]}
        suffix = f" (달력 {raw_days:g}일, 휴장·주말 제외)" if raw_days is not None else ""
        if raw_days is not None:
            snap["metrics"][name]["calendar_days"] = raw_days
        if st == "breach":
            breaches.append(f"{label}({name})={val:g} ≥ {breach:g}{suffix}")
        elif st == "warn":
            warns.append(f"{label}({name})={val:g} ≥ {warn:g}{suffix}")

    # ── 수집 경로(스크랩) 자체의 건강 ─────────────────────────────────────────
    # WHY(실측 2026-09-28 18:11~18:21 KST): postgres-exporter 스크랩이 scrape_timeout 45초에 걸려
    #   **11회 연속 실패**했다(up=0, scrape_duration 45.009초, 모든 컬렉터가 내부 60초 데드라인 초과).
    #   그 사이 dq_* 86개 시리즈는 Prometheus 5분 lookback 을 넘겨 stale(실명)이 됐는데, 그 창에 이
    #   틱이 한 번도 돌지 않아(18:09:30 → 18:23:27) **어떤 틱도 실명을 보고하지 않았다** — 3시간 뒤
    #   수동 조사로 발견했다. dq_* 소실은 5분 뒤에야 드러나므로 그 **직전 단계**를 직접 판정한다.
    #   Alertmanager 가 없어(백로그 R15) 이 틱이 유일한 통보 경로라는 점이 이 판정의 이유다.
    #   ⚠ 타임아웃을 50~55초로 늘리는 것은 수리가 아니다: 컬렉터 내부 데드라인이 60초라 쿼리가
    #     멈추면 연장해도 실패하고, 늘린 만큼 실명이 더 늦게 드러난다(문턱을 낮춰 숨기는 것과 같다).
    #   ⚠ 단발 실패는 breach 로 세지 않는다: exporter 재기동 직후 ~26초는 정상 과도기다(실측).
    #     단발/지속은 6분 창의 **실패 횟수**로 가른다(1회 = 과도기 warn, 2회 이상 = breach).
    #     평균이 아니라 횟수인 이유는 아래 classify_scrape_path 주석에 실측값과 함께 적어 두었다.
    sp = {"job": SCRAPE_JOB, "up": None, "duration_s": None, "status": "nodata",
          "warn_s": SCRAPE_WARN_S, "breach_s": SCRAPE_BREACH_S}
    up_vals, _ = instant(f'up{{job="{SCRAPE_JOB}"}}')
    cnt_vals, _ = instant(f'count_over_time(up{{job="{SCRAPE_JOB}"}}[{SCRAPE_WINDOW}])')
    ok_vals, _ = instant(f'sum_over_time(up{{job="{SCRAPE_JOB}"}}[{SCRAPE_WINDOW}])')
    sd_vals, _ = instant(SCRAPE_SERIES)
    sp["up"] = max(up_vals) if up_vals else None
    sp["scrapes_6m"] = max(cnt_vals) if cnt_vals else None
    sp["ok_6m"] = max(ok_vals) if ok_vals else None
    sp["duration_s"] = max(sd_vals) if sd_vals else None
    sp["status"], _msg = classify_scrape_path(sp["up"], sp["scrapes_6m"], sp["ok_6m"], sp["duration_s"])
    if _msg:
        # status=nodata(판정 불가)는 위반으로 단정하지 않고 경고로 올린다 — 다만 조용히는 넘기지 않는다.
        (breaches if sp["status"] == "breach" else warns).append(_msg)
    snap["scrape_path"] = sp

    # ── 모니터링 사각지대 판정 ────────────────────────────────────────────────
    # WHY: nodata 를 그냥 넘기면 완전 실명이 rc=0 으로 "정상" 보고된다 — 실측 2026-09-25 20:01:
    #   postgres-exporter 스크랩이 12.7초인데 Prometheus scrape_timeout 이 10초여서 매 스크랩이
    #   실패 → dq_* 51개 전부 소실 → dq_snapshot 은 17개 전부 '없음' 인데 rc=0 을 반환했다.
    #   "실패할 수 없는 check 는 check 가 아니다"의 같은 함정: 판정에서 nodata 를 빼면
    #   모니터링이 죽은 것이 모니터링이 잘 도는 것과 구분되지 않는다.
    core_missing = [n for n in nodata if n in CORE_ALWAYS]
    n_total = len(SPECS)
    if core_missing or len(nodata) / n_total >= 0.3:
        severity, msg = classify_blackout(nodata, n_total, core_missing, _host_uptime_s())
        (breaches if severity == "breach" else warns).append(msg)
    snap["nodata"] = nodata
    snap["nodata_core"] = core_missing

    # ── 출력 ──
    print(f"[dq_snapshot] {now.isoformat(timespec='seconds')} (추세 {a.hours:g}h)")
    if nodata:
        print(f"  [!!] 조회 실패(nodata) {len(nodata)}/{n_total}: " + ", ".join(nodata))
    _u = "없음" if sp["up"] is None else f"{sp['up']:g}"
    _d = "없음" if sp["duration_s"] is None else f"{sp['duration_s']:.1f}s"
    _c = (f"{sp['ok_6m']:g}/{sp['scrapes_6m']:g}성공" if sp["ok_6m"] is not None and sp["scrapes_6m"] is not None
          else "창 없음")
    print(f"  [경로] 스크랩 up={_u} 지연={_d} ({SCRAPE_WINDOW} {_c}) "
          f"(warn {SCRAPE_WARN_S:g} / 타임아웃 {SCRAPE_BREACH_S:g})")
    for name, m in snap["metrics"].items():
        v = "없음" if m["value"] is None else f"{m['value']:g}"
        mark = {"ok": "OK  ", "warn": "WARN", "breach": "위반", "info": "·   ", "nodata": "데이터X"}[m["status"]]
        print(f"  [{mark}] {m['label']:22s} {v:>12s}")
    # [정보] 상시 보고 — '·' 접두어가 곧 틱(researcher_cycle.snapshot_brief)이 뽑아 가는 형식이다.
    for _n, _note in ALWAYS_REPORT.items():
        _m = snap["metrics"].get(_n) or {}
        _v = "없음" if _m.get("value") is None else f"{_m['value']:.2f}"
        print(f"· [정보] {_m.get('label', _n)}({_n})={_v}{_note}")
    if breaches:
        print(f"  ★ 위반 {len(breaches)}: " + " | ".join(breaches))
    if warns:
        print(f"  · 경고 {len(warns)}: " + " | ".join(warns))
    snap["breaches"] = breaches
    snap["warns"] = warns

    os.makedirs(OUTDIR, exist_ok=True)
    stamp = now.strftime("%Y%m%d-%H%M%S")
    jpath = os.path.join(OUTDIR, f"dq_{stamp}.json")

    # ── 차트 ──
    png = None
    if not a.no_chart:
        series_by = {}
        # 스크랩 경로 패널을 **맨 앞**에 둔다 — 모니터링이 눈먼 상태를 첫 화면에서 본다.
        _ss = range_series(SCRAPE_SERIES, a.hours)
        if _ss:
            series_by["scrape_duration_postgres"] = (_ss, "postgres scrape duration (s)",
                                                    SCRAPE_WARN_S, SCRAPE_BREACH_S)
        for name, agg, warn, breach, label in SPECS:
            s = range_series(name, a.hours)
            if s:
                series_by[name] = (s, label, warn, breach)
        if series_by:
            keys = list(series_by)[:13]
            cols = 3
            rows = (len(keys) + cols - 1) // cols
            fig, axes = plt.subplots(rows, cols, figsize=(5.2 * cols, 3.0 * rows), squeeze=False)
            for i, name in enumerate(keys):
                ax = axes[i // cols][i % cols]
                s, label, warn, breach = series_by[name]
                # 신선도 패널은 **거래일 지연**으로 그린다 — 달력일로 그리면 주말·휴장이
                # 문턱선(warn 1 / breach 2)과 축이 어긋나 눈에 잘못 읽힌다(수치와 같은 단위로).
                conv = (lambda t, v: float(trading_days_behind(v, t.date(), holidays, now=t) or 0)) \
                    if (name == "market_data_freshness_days" and hol_ok) else None
                for ser in s[:4]:
                    lb = ",".join(f"{k}={v}" for k, v in list(ser["labels"].items())[:2]) or name
                    xs = [p[0] for p in ser["points"]]
                    ys = [conv(p[0], p[1]) if conv else p[1] for p in ser["points"]]
                    ax.plot(xs, ys, marker=".", linewidth=1.2, label=lb[:22])
                if breach is not None:
                    ax.axhline(breach, color="crimson", linestyle="--", linewidth=1,
                               label=f"breach {breach:g}")
                if warn is not None:
                    ax.axhline(warn, color="orange", linestyle=":", linewidth=1,
                               label=f"warn {warn:g}")
                ax.set_title(EN.get(name, name), fontsize=10)
                ax.grid(alpha=0.25)
                ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
                for lb in ax.get_xticklabels():
                    lb.set_rotation(30)
                    lb.set_fontsize(7)
                ax.tick_params(axis="y", labelsize=8)
                if s[0]["labels"] or breach is not None:
                    ax.legend(fontsize=6, loc="best")
            for j in range(len(keys), rows * cols):
                axes[j // cols][j % cols].axis("off")
            fig.suptitle(f"analyist_dd DQ / ingestion monitoring  {now.strftime('%Y-%m-%d %H:%M')} KST"
                         f"   (breach {len(breaches)} / warn {len(warns)})", fontsize=12)
            fig.tight_layout(rect=(0, 0, 1, 0.97))
            png = os.path.join(OUTDIR, f"dq_{stamp}.png")
            fig.savefig(png, dpi=110)
            plt.close(fig)
            snap["chart"] = os.path.relpath(png, PROJ)
            print(f"  차트: {snap['chart']}")

    with open(jpath, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=2)
    with open(HISTORY, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": snap["ts"], "breaches": breaches, "warns": warns,
                            "nodata": len(nodata), "nodata_core": len(snap["nodata_core"]),
                            "values": {k: v["value"] for k, v in snap["metrics"].items()
                                       if v.get("value") is not None},
                            "scrape_up": sp["up"], "scrape_duration_s": sp["duration_s"],
                            "chart": snap.get("chart")}, ensure_ascii=False) + "\n")

    _prune(OUTDIR)   # 스냅샷 보존 정책(최근 48개) — 무한 증가 방지
    if breaches:
        return 3
    if warns:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
