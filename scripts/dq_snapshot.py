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
matplotlib.use("Agg")
import matplotlib.dates as mdates   # noqa: E402
import matplotlib.pyplot as plt     # noqa: E402
from matplotlib import font_manager  # noqa: E402
from _paths import proj, proj_dir

PROM = os.environ.get("PROM_URL", "http://127.0.0.1:9090")








BLIND_STREAK = 5
_API_STATE = {"fail_streak": 0, "blind": False}


def reset_api_state():
    _API_STATE["fail_streak"] = 0
    _API_STATE["blind"] = False

PROJ = os.environ.get("RES_PROJ", proj_dir())
OUTDIR = os.path.join(PROJ, "data/reports/dq_snapshots")
HISTORY = os.path.join(OUTDIR, "history.jsonl")
KST = timezone(timedelta(hours=9))


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


SPECS = [









    ("market_data_freshness_days", "max", 1.0, 2.0, "데이터 신선도(거래일)"),
    ("market_data_rows_recent", "sum", None, None, "최근 적재 행수"),
    ("market_data_zero_volume_ratio_20d", "max", 0.05, 0.10, "거래량0 비율"),
    ("market_data_frozen_ratio_20d", "max", 0.05, 0.15, "동결(가격 불변) 비율"),
    ("dq_asof_violation_rows", "sum", None, 0.0, "as-of 위반 행수"),
    ("dq_claim_parse_failure", "sum", None, 0.0, "러너 파서 실패"),








    ("dq_claim_gap", "sum", None, None, "자기신고 갭(중복재수집 포함·정보용)"),
    ("dq_claim_source", "sum", None, None, "소스 수신 행수"),




    ("dq_feature_stock_constant_ratio", "max", 0.45, 0.50, "종목상수 피처 비율"),






















    ("dq_feature_coverage_illusion_count_model_window", "max", 30.0, 35.0, "모델 창(120일) 커버리지 착시 피처 수(기준선 26)"),
    ("dq_feature_coverage_illusion_count", "max", None, None, "커버리지 착시 피처 수(전 창·정보용 — 라벨 드리프트로 26↔63)"),
    ("dq_feature_null_ratio_high_count", "max", 32.0, 45.0, "결측90%↑ 피처 수(기준선 30)"),
    ("dq_feature_coverage_illusion_max", "max", None, None, "커버리지 착시 최대(정보용)"),
    ("dq_feature_null_ratio_max", "max", None, None, "피처 결측 최대(정보용)"),





    ("dq_feature_oldest_age_days", "max", None, None, "가장 오래된 피처 측정 나이(일)"),
    ("dq_feature_market_level_count", "max", None, None, "시장레벨 피처 수"),
    ("dq_padding_rows_before_listing", "max", None, 0.0, "상장 전 행(padding)"),



    ("news_analysis_freshness_hours", "max", 2.0, 6.0, "뉴스 저장 신선도(시간)"),
    ("news_analysis_freshness_rows_24h", "sum", None, None, "뉴스 24h 저장 행수"),
    ("feature_alive_count", "max", None, None, "살아있는 피처"),
    ("feature_dead_count", "max", None, None, "죽은 피처"),





    ("dq_feature_alive_xsec_count", "max", None, None, "횡단면 변별력 있는 살아있는 피처(시장레벨 제외)"),
    ("dq_feature_feature_count", "max", None, None, "feature_coverage 행수(정보용 — 창 인구 판정의 분모)"),





    ("dq_feature_window_variants_count", "max", None, None, "창 정의 가짓수(1=일관·정보용)"),
    ("dq_feature_non_model_window_rows", "max", None, None, "모델 창(120일) 아닌 피처 행수(정보용)"),
    ("dq_feature_illusion_at_120d_count", "max", None, None, "모델 창 120일 기준 착시 수(정보용)"),
]





ALWAYS_REPORT = {
    "dq_feature_oldest_age_days": " — R14 feature_coverage 정체(09-24 고정 128행) 감시",


    "dq_feature_non_model_window_rows": " — 창 혼재(야간 빌더가 격자 스팬 창으로 기록) 감시: >0 이면 착시 지표가 62 쪽으로 복귀 중",
}



SILENT_AT_ZERO = {"dq_feature_non_model_window_rows"}


def blind_instrument_warns(metrics: dict) -> list:
    """'계기가 꺼져 있어 통과'인 경우를 경고로 올린다(실측 2026-11-24).

    `dq_feature_coverage_illusion_count_model_window` 의 판정 인구는 **모델 창(120일)** 행이다.
    그런데 11-24 20:1x 실측에서 feature_coverage 209행이 전부 창 30일(166) · 525일(24) · 42일(19)
    이고 **120일 행이 0개**였다 → 게이트는 0(=ok)으로 읽히지만 이는 '착시가 없다'가 아니라
    '잴 인구가 없다'다. 같은 이유로 `dq_feature_illusion_at_120d_count` 도 0 이 된다.
    0 을 정상으로 보고하면 window-mix 결함(R38)이 조용해지므로, 인구가 비면 경고로 올린다.
    """
    m = metrics or {}
    fc = (m.get("dq_feature_feature_count") or {}).get("value")
    if not fc:
        alive = (m.get("feature_alive_count") or {}).get("value") or 0
        dead = (m.get("feature_dead_count") or {}).get("value") or 0
        fc = (alive + dead) or None
    nm = (m.get("dq_feature_non_model_window_rows") or {}).get("value")
    ill_all = (m.get("dq_feature_coverage_illusion_count") or {}).get("value")
    ill_model = (m.get("dq_feature_coverage_illusion_count_model_window") or {}).get("value")
    if not fc or nm is None:
        return []
    if nm >= fc:
        return [f"계기판 실명: 모델 창(120일) 커버리지 행 0/{fc:g} — 착시 게이트 "
                f"{0 if ill_model is None else ill_model:g} 는 '착시 없음'이 아니라 '판정 인구 없음'"
                f"(전 창 착시 {0 if ill_all is None else ill_all:g})"]
    return []


def always_report_lines(metrics: dict) -> list:
    """ALWAYS_REPORT 의 [정보] 줄을 만든다(출력과 분리해 회귀 테스트가 같은 코드를 검증하게).

    SILENT_AT_ZERO 는 값이 **정확히 0** 일 때만 침묵한다 — nodata(None)는 '없음'으로 반드시 올린다
    (모니터링 실명을 0 으로 숨기지 않는다).
    """
    out = []
    for _n, _note in ALWAYS_REPORT.items():
        _m = metrics.get(_n) or {}
        if _n in SILENT_AT_ZERO and _m.get("value") == 0:
            continue
        _v = "없음" if _m.get("value") is None else f"{_m['value']:.2f}"
        out.append(f"· [정보] {_m.get('label', _n)}({_n})={_v}{_note}")
    return out









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



SCRAPE_JOB = "postgres"




SCRAPE_WARN_S = 60.0
SCRAPE_BREACH_S = 95.0
SCRAPE_SERIES = 'scrape_duration_seconds{job="postgres"}'
SCRAPE_WINDOW = "6m"
SCRAPE_FAILS_BREACH = 2


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





DUE_HOUR = 2





DUE_TODAY_HOUR = 22


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
    if now is None:
        back = 1
    elif now.hour >= DUE_TODAY_HOUR:
        back = 0
    elif now.hour >= DUE_HOUR:
        back = 1
    else:
        back = 2
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
    if _API_STATE["blind"]:
        return {}
    url = f"{PROM}{path}?" + urllib.parse.urlencode(params)
    try:
        with opener(url).open(url, timeout=20) as r:
            data = json.load(r)
        _API_STATE["fail_streak"] = 0
        return data
    except (OSError, json.JSONDecodeError):
        _API_STATE["fail_streak"] += 1
        if _API_STATE["fail_streak"] >= BLIND_STREAK and not _API_STATE["blind"]:
            _API_STATE["blind"] = True
            print(f"[사각지대] Prometheus 연속 {BLIND_STREAK}회 조회 실패 — 남은 조회를 건너뛴다"
                  f"(전 메트릭 nodata 로 판정)", flush=True)
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
                raw_days = val
                td = trading_days_behind(raw_days, now.date(), holidays, now=now)
                if td is not None:
                    val = float(td)
            else:



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

        (breaches if sp["status"] == "breach" else warns).append(_msg)
    snap["scrape_path"] = sp







    core_missing = [n for n in nodata if n in CORE_ALWAYS]
    n_total = len(SPECS)
    if core_missing or len(nodata) / n_total >= 0.3:
        severity, msg = classify_blackout(nodata, n_total, core_missing, _host_uptime_s())
        (breaches if severity == "breach" else warns).append(msg)
    snap["nodata"] = nodata
    snap["nodata_core"] = core_missing


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

    for _line in always_report_lines(snap["metrics"]):
        print(_line)
    if breaches:
        print(f"  ★ 위반 {len(breaches)}: " + " | ".join(breaches))
    warns.extend(blind_instrument_warns(snap["metrics"]))
    if warns:
        print(f"  · 경고 {len(warns)}: " + " | ".join(warns))
    snap["breaches"] = breaches
    snap["warns"] = warns

    os.makedirs(OUTDIR, exist_ok=True)
    stamp = now.strftime("%Y%m%d-%H%M%S")
    jpath = os.path.join(OUTDIR, f"dq_{stamp}.json")


    png = None
    if not a.no_chart:
        series_by = {}

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

    _prune(OUTDIR)
    if breaches:
        return 3
    if warns:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
