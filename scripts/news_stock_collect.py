#!/usr/bin/env python3
"""news_stock_collect — Google News RSS 로 종목별 뉴스 언급을 모은다 (파일럿, LLM 비용 0).

WHY (2026-11-19 실측): 패널 200종목 중 뉴스가 붙은 종목은 31개뿐이었다(commit 39e94d6 측정).
기존 RSS 수집기는 **피드 단위**(사이트별)로 받아 기사가 언급한 종목만 잡히고 종목별 질의가 없어
뉴스·감성 피처가 상수 0(또는 nnz 0.02%)이 됐다. 이 러너는 종목명으로 Google News RSS 를
**종목별 질의**해 '언급 건수' 원장을 만든다(감성은 다음 단계). 스펙 docs/spec_news_stock_pilot.md §1.

실측 근거 (2026-11-19 조회·실험, 본 파일 작성 시점 값):
  - 호스트 /usr/bin/python3 에 feedparser **없음**(ModuleNotFoundError 실측) → stdlib ElementTree 파싱.
  - stocks 조회: 패널 200코드 전원 존재. 이름 정규화 길이 <3 인 종목 18개(금비·엔젯·'YW' 등),
    '우' 우선주 9개(대상우·현대차우·CJ제일제당 우 등).
    조회: `SELECT stock_code, stock_name FROM stocks WHERE stock_code = ANY(%s::text[])` (panel_codes.txt)
  - 우선주: 기저명이 stocks.stock_name 에 실제 존재하는 경우에만 '우'를 떼어 질의·매칭한다
    ('대상우'→'대상' 존재 ✓, '성우'→'성' 부재 → 원명 유지). 뉴스 제목에 '우' 클래스는 안 나온다.
  - 현대차 14일 창 실호출 1회: item **100건 = 창 상한** 도달 → 7일 반창 재분할 필요.
    명령: curl -s -A "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36" \
      "https://news.google.com/rss/search?q=현대차+after:2026-11-05+before:2026-11-18&hl=ko&gl=KR&ceid=KR:ko"
  - 같은 100건 표본으로 정밀도 가드 측정: 통과 95 / rejected 5 ('현대자동차그룹…' 제목은
    '현대차'가 연속 부분문자열이 아니라 불통과). pubDate 는 RFC822 GMT, min/max 실측으로
    after:/before: 양 끝 **포함**(2026-11-05 08:00 ~ 2026-11-18 08:00).
  - RSS 구조: <item><title>,<link>,<pubDate>,<source url=..>발행사</source> — ElementTree 로
    100/100 파싱 성공 실측.

설계:
  - 정밀도 가드(스펙 §1, 필수): 정규화 이름(공백·(주)·㈜·주식회사 제거, 대문자 —
    services/news-analyzer/app/storage/postgres_storage.py:37 normalize_stock_name 와 동일 규칙)
    길이 >=3 이면 제목 정규화에 부분문자열, <3 이면 제목에서 **독립 토큰**(양옆이
    [A-Za-z0-9가-힣] 아님)일 때만 저장. 불일치는 rejected 로만 집계.
  - 멱등: news_stock_mentions PK(stock_code, url) + ON CONFLICT DO NOTHING — 재실행 안전.
  - 진행 커서 data/state/news_stock_collect_cursor.json: 완료한 (종목,창)의 종목별
    '완료 기준일'을 저장. 키(코드파일·코드 목록 해시·기간·창 크기)가 바뀌면 무시.
  - 창 상한(100건) 도달 시 반창 재분할(scripts/news_revive_pipeline.py fetch_window 와 동일 정책,
    위 실측이 근거). 재분할은 결정적(중간 날짜)이라 재개 시 창 경계가 재현된다.
  - 과호출 방지: --sleep **하한 1.2s 강제**(스펙), --max-queries 초과 시 정상 종료(exit 0,
    커서 저장 후 다음 실행이 이어감). net_guard 는 파일럿이라 미배선(스펙 미요구) — 대신
    요청 사이 sleep + 실행당 쿼리 상한으로 지킨다.
  - 자기신고(3분리): dq_claim.claim_start/claim_finish — source_rows=소스가 준 기사 수,
    claimed_rows=가드 통과(파서 생성) 수, persisted_rows=실제 삽입 수(중복 제외).
  - --dry-run: RSS 는 조회하되 DB DDL/DML·커서·자기신고에 **아무것도 쓰지 않는다**.

사용 (호스트 /usr/bin/python3):
  cd /home/jhshi/analyist_dd && set -a && . ./.env && set +a
  export POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5434
  /usr/bin/python3 scripts/news_stock_collect.py --codes-file reports/panel_codes.txt \\
      --from 2026-08-19 --to 2026-11-18 --max-queries 2000
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import psycopg2  # noqa: E402

try:  # 자기신고(3분리) — 없으면 수집은 그대로 진행하고 자기신고만 생략한다
    from dq_claim import claim_start, claim_finish  # noqa: E402
except ImportError:
    claim_start = claim_finish = None

PG = dict(host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
          port=int(os.environ.get("POSTGRES_PORT", "5434")),
          dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
          user=os.environ.get("POSTGRES_USER", "stock_user"),
          password=os.environ.get("POSTGRES_PASSWORD", ""))

CURSOR_PATH = os.path.join(os.environ.get("PROJ_DIR", "."), "data", "state",
                           "news_stock_collect_cursor.json")

FEED_CAP = 100          # Google News RSS 창당 상한 (실측: 현대차 14일 창 100건)
SLEEP_FLOOR = 1.2       # 스펙 §5: --sleep 하한
FETCH_RETRY_MAX = 3     # 일시 오류 재시도 횟수(총 시도)
FETCH_BACKOFF = 2.0     # 재시도 간격 배수(초)
FEED_TIMEOUT = 20

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

DDL = """
CREATE TABLE IF NOT EXISTS news_stock_mentions (
    stock_code     TEXT        NOT NULL,
    url            TEXT        NOT NULL,
    title          TEXT        NOT NULL,
    published_date DATE,
    source         TEXT,
    matched_by     TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (stock_code, url)
)
"""

INSERT_SQL = """
INSERT INTO news_stock_mentions
    (stock_code, url, title, published_date, source, matched_by)
VALUES (%s, %s, %s, %s, %s, %s)
ON CONFLICT (stock_code, url) DO NOTHING
"""

_PREF_SUFFIX = re.compile(r"\s*우$")


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


class Stats:
    """수집 실행 카운터 — 자기신고(3분리)의 입력 값이다."""

    def __init__(self):
        self.queries = 0     # 실제 HTTP 요청 수(재시도 포함)
        self.seen = 0        # 소스가 준 기사 수
        self.accepted = 0    # 정밀도 가드 통과 수(파서 생성 행수)
        self.inserted = 0    # 실제 삽입 행수(중복은 0)
        self.rejected = 0    # 가드 불통과 수(집계만)
        self.failed = 0      # 조회 실패한 (종목,창) 수
        self.windows = 0     # 완료한 (종목,창) 수


def normalize_stock_name(name):
    """종목명 정규화 키 — 공백·(주)·㈜·주식회사 제거 후 대문자.

    services/news-analyzer/app/storage/postgres_storage.py:37 normalize_stock_name 과
    동일 규칙(그 함수의 docstring 실측: stocks.stock_name 에는 '(주)' 등 0건이므로
    사실상 제목 쪽 정규화로만 효과가 있다).
    """
    if not name:
        return ""
    return (str(name).replace(" ", "").replace("(주)", "")
            .replace("㈜", "").replace("주식회사", "").upper())


def resolve_query_name(name, all_names):
    """질의·매칭에 쓸 종목명 — 우선주 '우' 접미는 기저명이 stocks 에 존재할 때만 제거.

    실측(2026-11-19, stocks 4,352개 이름 전체 조회): 패널의 '우' 종료 종목 9개 중
    '대상우'→'대상', '현대차우'→'현대차' 등 8개는 기저명 존재, '성우'→'성' 은 부재라
    원명을 유지한다. 뉴스 제목은 우선주를 기저명으로만 쓰므로 원명 질의는 빈 결과가 된다.
    """
    if not name:
        return name
    base = _PREF_SUFFIX.sub("", name)
    if base and base != name and base in all_names:
        return base
    return name


def match_kind(name_norm):
    """가드 통과 방식을 matched_by 값으로 준다(스펙: <3자면 독립 토큰)."""
    return "token" if len(name_norm) < 3 else "name"


def title_matches(title, name_norm):
    """정밀도 가드(스펙 §1): 기사 제목에 종목명이 그대로 포함될 때만 통과.

    이름 정규화 길이 >=3 → 제목 정규화의 부분문자열. <3 → 독립 토큰(양옆이 한글·영숫자
    아님)일 때만. 실측(2026-11-19, 현대차 100건 표본): 통과 95 / rejected 5 —
    '현대자동차그룹…' 제목은 '현대차'가 연속 부분문자열이 아니어서 불통과다.
    """
    if not name_norm:                      # 빈 이름은 아무것도 매칭하지 않는다(빈 토큰 전건 매칭 방지)
        return False
    if len(name_norm) < 3:
        return re.search(r"(?<![A-Za-z0-9가-힣])" + re.escape(name_norm)
                         + r"(?![A-Za-z0-9가-힣])", title) is not None
    return name_norm in normalize_stock_name(title)


def _norm_url(url):
    """URL 정규화 — PK 로 쓰므로 결정적이어야 한다(같은 기사 = 같은 값).

    scripts/news_revive_pipeline.py _norm_url 과 동일: 500자 초과는 sha256 해시로 대체
    (Google 리다이렉트 URL 은 수백 자가 될 수 있다).
    """
    if not url:
        return None
    if len(url) <= 500:
        return url
    return "hash:" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:40]


def parse_rss(xml_bytes):
    """Google News RSS 를 entry dict 목록으로 — stdlib ElementTree(의존성 0).

    실측(2026-11-19, 현대차 창 100건 표본): title/link/pubDate(RFC822 GMT)/source 텍스트
    전부 추출되고 pubDate 100/100 파싱 성공. XML 이 깨졌으면 ET.ParseError 가 난다.
    """
    root = ET.fromstring(xml_bytes)
    out = []
    for it in root.findall("./channel/item"):
        title = (it.findtext("title") or "").strip()
        pub = None
        pub_raw = it.findtext("pubDate")
        if pub_raw:
            try:
                pub = parsedate_to_datetime(pub_raw)
            except (TypeError, ValueError):
                pub = None
        src = it.find("source")
        out.append({
            "title": title,
            "url": _norm_url((it.findtext("link") or "").strip()),
            "published": pub,
            "source": (src.text or "").strip() if src is not None and src.text else None,
        })
    return out


def google_news_search(name, ws, we, timeout=FEED_TIMEOUT):
    """Google News RSS 종목명 질의. entry 목록 반환, 실패 시 None(빈 결과 []와 구분).

    실측(2026-11-19): after:/before: 는 **양 끝 포함**(min/max pubDate 로 확인).
    """
    q = f"{name} after:{ws.isoformat()} before:{we.isoformat()}"
    url = ("https://news.google.com/rss/search?q=" + urllib.parse.quote(q)
           + "&hl=ko&gl=KR&ceid=KR:ko")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return parse_rss(resp.read())
    except Exception as exc:  # noqa: BLE001 - 네트워크·XML 오류는 창 실패로 처리(재시도는 호출부)
        log(f"RSS 조회 실패 {name} {ws}~{we}: {type(exc).__name__}: {exc}")
        return None


def iter_windows(start, end, days=14):
    """[ws, we] **포함** 날짜창 생성(scripts/news_revive_pipeline.py iter_windows 와 동일)."""
    d = start
    while d <= end:
        we = d + timedelta(days=days)
        if we - timedelta(days=1) > end:
            we = end + timedelta(days=1)
        yield d, we - timedelta(days=1)
        d = we


def split_window(ws, we):
    """창을 결정적으로 반씩 나눈다 — 재개 시 창 경계가 재현돼야 커서가 맞다."""
    mid = ws + timedelta(days=(we - ws).days // 2)
    return (ws, mid), (mid + timedelta(days=1), we)


def insert_mention(cur, stock_code, url, title, published, source, matched_by):
    """멱등 삽입(ON CONFLICT DO NOTHING). 실제 삽입 행수(0 또는 1)를 돌려준다."""
    cur.execute(INSERT_SQL, (stock_code, url, title, published, source, matched_by))
    return max(int(getattr(cur, "rowcount", 0) or 0), 0)


def read_codes(path):
    """코드 파일 읽기 — 빈 줄·주석 제거, 순서 유지한 중복 제거. 실패 시 None."""
    try:
        with open(path, encoding="utf-8") as f:
            lines = [ln.strip() for ln in f
                     if ln.strip() and not ln.strip().startswith("#")]
    except OSError as exc:
        log(f"종목코드 파일을 읽지 못했다: {path} ({exc})")
        return None
    out, seen = [], set()
    for code in lines:
        if code not in seen:
            seen.add(code)
            out.append(code)
    return out


# ── resume 커서 ────────────────────────────────────────────────────────────
# WHY: 러너는 (종목,창)을 처음부터 훑고 --max-queries 에서 멈춘다. 커서가 없으면 예산이
# 작은 실행이 **매번 같은 앞 구간만 다시 받고** 뒤 구간을 못 채운다(dart_disclosure_backfill
# 의 같은 교훈, 2026-10-03). 종목별 '완료 기준일'을 저장해 다음 실행이 그 다음 창부터 돈다.
# 키(코드파일·코드 해시·기간·창 크기)가 다르면 무시한다(다른 작업의 진도를 물려받지 않는다).

def _cursor_key(codes_file, codes, d_from, d_to, window_days):
    digest = hashlib.sha256(
        json.dumps(codes, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
    return json.dumps({"codes_file": os.path.abspath(codes_file),
                       "codes_sha": digest, "from": str(d_from), "to": str(d_to),
                       "window_days": int(window_days)}, ensure_ascii=False)


def load_cursor(key):
    """이전 실행이 완료한 종목별 기준일 {code: date}. 키가 다르거나 파일이 깨졌으면 {}."""
    try:
        with open(CURSOR_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("key") != key:
            return {}
        return {c: date.fromisoformat(v)
                for c, v in (data.get("done_through") or {}).items()}
    except (OSError, ValueError, TypeError):
        return {}


def save_cursor(key, done_through, total_codes):
    try:
        os.makedirs(os.path.dirname(CURSOR_PATH) or ".", exist_ok=True)
        tmp = CURSOR_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"key": key,
                       "done_through": {c: str(v) for c, v in done_through.items()},
                       "total_codes": total_codes,
                       "updated": datetime.now().isoformat(timespec="seconds")},
                      f, ensure_ascii=False)
        os.replace(tmp, CURSOR_PATH)
    except OSError as exc:  # 커서 실패가 수집을 막지는 않는다
        log(f"커서 저장 실패({exc!r}) — 다음 실행은 처음부터(멱등 삽입)")


def clear_cursor():
    with contextlib.suppress(OSError):
        os.remove(CURSOR_PATH)


def main():
    ap = argparse.ArgumentParser(description="종목별 Google News RSS 언급 수집(파일럿)")
    ap.add_argument("--codes-file", required=True, help="종목코드 파일(한 줄에 하나)")
    ap.add_argument("--from", required=True, dest="d_from", help="시작일 YYYY-MM-DD")
    ap.add_argument("--to", required=True, dest="d_to", help="종료일 YYYY-MM-DD(포함)")
    ap.add_argument("--window-days", type=int, default=14,
                    help="질의 창 크기(일, 기본 14 — 창당 100건 상한 실측)")
    ap.add_argument("--sleep", type=float, default=SLEEP_FLOOR,
                    help="질의 간 간격(초, 기본 1.2 — 스펙 하한, 더 작아도 1.2로 올린다)")
    ap.add_argument("--max-queries", type=int, default=2000,
                    help="실행당 HTTP 요청 상한 — 초과 시 정상 종료(커서로 이어감)")
    ap.add_argument("--limit-codes", type=int, default=0,
                    help="0보다 크면 앞 N 개 종목만 처리(검증용)")
    ap.add_argument("--dry-run", action="store_true",
                    help="RSS 는 조회하되 DB·커서·자기신고에 아무것도 쓰지 않는다")
    a = ap.parse_args()

    try:
        d_from = datetime.strptime(a.d_from, "%Y-%m-%d").date()
        d_to = datetime.strptime(a.d_to, "%Y-%m-%d").date()
    except ValueError:
        log(f"날짜 형식 오류: --from={a.d_from} --to={a.d_to} (YYYY-MM-DD)")
        return 2
    if d_from > d_to:
        log(f"기간 오류: from({d_from}) > to({d_to})")
        return 2
    if a.window_days < 1:
        log("--window-days 는 1 이상이어야 한다")
        return 2
    sleep_eff = max(a.sleep, SLEEP_FLOOR)
    if sleep_eff != a.sleep:
        log(f"--sleep {a.sleep} 은 하한 미만 — {sleep_eff}s 로 올린다(스펙 §5)")

    codes = read_codes(a.codes_file)
    if not codes:
        return 2
    if a.limit_codes > 0:
        codes = codes[:a.limit_codes]

    try:
        conn = psycopg2.connect(**PG)
    except psycopg2.Error as exc:
        log(f"DB 연결 실패: {exc}")
        return 2
    cur = conn.cursor()

    if not a.dry_run:
        cur.execute(DDL)
        conn.commit()
        if claim_start is not None:
            claim_start("news_stock_collect", "news_stock_mentions",
                        note=f"codes_file={a.codes_file} {d_from}~{d_to} w={a.window_days}")

    # 종목명 조회 + 우선주 기저명 해석 (읽기만 — dry-run 도 수행)
    cur.execute("SELECT stock_code, stock_name FROM stocks "
                "WHERE stock_code = ANY(%s::text[])", (codes,))
    names_by_code = dict(cur.fetchall())
    cur.execute("SELECT stock_name FROM stocks")
    all_names = {row[0] for row in cur.fetchall()}

    plan = []
    missing = 0
    for code in codes:
        name = names_by_code.get(code)
        if not name:
            missing += 1
            continue
        plan.append((code, resolve_query_name(name, all_names)))
    if missing:
        log(f"경고: stocks 에 없는 코드 {missing}개는 건너뛴다")
    log(f"기간 {d_from} ~ {d_to} ({len(list(iter_windows(d_from, d_to, a.window_days)))}개 창) "
        f"× 종목 {len(plan)}, 예산 {a.max_queries}콜, 간격 {sleep_eff}s"
        f"{' [DRY-RUN]' if a.dry_run else ''}")

    ckey = _cursor_key(a.codes_file, codes, d_from, d_to, a.window_days)
    done_through = load_cursor(ckey)
    if done_through:
        fully = sum(1 for v in done_through.values() if v >= d_to)
        log(f"커서 재개: {fully}/{len(plan)} 종목은 완료 — 완료 기준일 이후 창만 돈다")

    st = Stats()
    hit_limit = False

    def fetch_retry(name, ws, we):
        """일시 오류는 재시도. 모든 HTTP 요청 뒤에는 간격(sleep)을 지킨다."""
        for attempt in range(FETCH_RETRY_MAX):
            st.queries += 1
            if attempt:
                log(f"{name} {ws}~{we} 일시 오류 — 재시도 {attempt}/{FETCH_RETRY_MAX - 1}")
                time.sleep(FETCH_BACKOFF * attempt)
            entries = google_news_search(name, ws, we)
            time.sleep(sleep_eff)
            if entries is not None:
                return entries
        return None

    def handle_article(code, name_norm, entry):
        st.seen += 1
        if not title_matches(entry["title"], name_norm):
            st.rejected += 1
            return
        st.accepted += 1
        if a.dry_run:
            return
        st.inserted += insert_mention(
            cur, code, entry["url"], entry["title"],
            entry["published"].date() if entry["published"] else None,
            entry["source"], match_kind(name_norm))

    def process_window(code, name_norm, name, ws, we):
        """창 1개 처리. 'limit'=예산 소진, True=완료, False=실패(다음 실행에서 재시도)."""
        if st.queries >= a.max_queries:
            return "limit"
        entries = fetch_retry(name, ws, we)
        if entries is None:
            st.failed += 1
            return False
        if len(entries) >= FEED_CAP and (we - ws).days >= 7:
            # 창 상한 도달 → 반창 재분할(실측: 현대차 14일 창 100건). 경계는 결정적.
            for ws2, we2 in split_window(ws, we):
                res = process_window(code, name_norm, name, ws2, we2)
                if res is not True:
                    return res
            return True
        for entry in entries:
            handle_article(code, name_norm, entry)
        return True

    for code, qname in plan:
        name_norm = normalize_stock_name(qname)
        through = done_through.get(code) or d_from - timedelta(days=1)
        for ws, we in iter_windows(d_from, d_to, a.window_days):
            if we <= through:
                continue
            res = process_window(code, name_norm, qname, ws, we)
            if res == "limit":
                hit_limit = True
                break
            if res is True:
                st.windows += 1
                done_through[code] = we
                if not a.dry_run:
                    save_cursor(ckey, done_through, len(plan))
                    conn.commit()
            else:
                log(f"{code} {qname}: {ws}~{we} 조회 실패 — 다음 실행에서 재시도")
                break
        if hit_limit:
            break

    if not a.dry_run:
        conn.commit()
        if not hit_limit:
            clear_cursor()  # 전 구간 완료 → 커서 초기화

    if claim_finish is not None and not a.dry_run:
        claim_finish("news_stock_collect", source_rows=st.seen,
                     claimed_rows=st.accepted, persisted_rows=st.inserted,
                     note=(f"queries={st.queries} windows={st.windows} "
                           f"rejected={st.rejected} failed={st.failed} "
                           f"codes={len(plan)}/{len(codes)} missing={missing}"))

    log(f"완료: 질의 {st.queries} / 본 기사 {st.seen} / 가드 통과 {st.accepted} / "
        f"실제 삽입 {st.inserted} / rejected {st.rejected} / 창 {st.windows} / "
        f"종목 {len(plan)} (실패 창 {st.failed})")
    if st.seen > 0 and st.accepted == 0:
        log("★ 소스는 기사를 줬는데 가드 통과 0 — 종목명/질의 점검 필요")
    if hit_limit:
        log("쿼리 예산으로 중단 — 다음 실행에서 이어서(멱등 삽입)")
    if a.dry_run:
        log("dry-run: DB·커서·자기신고에 아무것도 쓰지 않았다")

    cur.close()
    conn.close()
    return 4 if (st.seen > 0 and st.accepted == 0) else 0


if __name__ == "__main__":
    sys.exit(main())
