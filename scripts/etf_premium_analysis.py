#!/usr/bin/env python3
"""레버리지/인버스 ETF 괴리율 당일 분석 — docs/spec_etf_premium_monitor.md §2.

WHY (2026-11-18 실측): 스냅샷(etf_premium_snapshots / data/etf_premium/*.jsonl)만으로는
계측 목표 ①②③(괴리 분포·평균회귀 반감기·괴리 발생 후 N분 수익률 기대값)에 닿지 않는다.
이 스크립트는 당일 스냅샷을 종목별 요약·연속 괴리 구간(episode)·사건연구(event study)로
집계해 **진입 규칙 후보 판정** 입력을 만든다(예: 디스카운트 −1% 이하 매수 시 +30분 기대값).

실측 조회 명령(2026-11-18 실행, DB 127.0.0.1:5434, 이 프로세스 환경변수 — 비밀번호 출력 금지):
  -- SELECT table_name FROM information_schema.tables WHERE table_schema='public' → 지수 테이블 없음
  -- SELECT column_name,data_type FROM information_schema.columns WHERE table_name='market_data'
  --   → stock_code·trade_date·open/high/low/close·volume·trading_value — 종목 행만
  -- SELECT COUNT(*) FROM market_data WHERE stock_code IN ('KOSPI','KOSDAQ','1001','2001') → 0
  -- SELECT DISTINCT indicator_name FROM macro_indicators → 기준금리·국고채3년·회사채3년·
  --   CNY·JPY·USD/KRW 환율·CPI·PPI·WTI 유가 9종 — 지수 등락 없음
  -- → §2.4 by_regime 은 지수 데이터가 없는 날 **regime: null + 사유**로 기록한다(추측 금지).

실측 조회 명령(2026-11-20 실행 — spec_etf_premium_fix.md 수정 계기, DB 127.0.0.1:5434):
  -- python3 - <<'EOF'  # jsonl 행 조회: 09:02 0193T0 행
  --   → price 4,255(전일 종가 유지·체결 0건) vs nav 3,683 → premium +15.5308%,
  --     volume 0. 09:04 체결 시작 후 정상화 → 개장 직후 미체결 행이 만든 가짜 괴리
  -- python3 - <<'EOF'  # DB 행 ts 초·마이크로초 분포
  --   → 09:00:02.450947 / 09:01:02.103983 / 09:02:01.756114 / 09:05:01.716161 …
  --     (크론 실행 지터 — 같은 분도 초·마이크로초가 다름)
  --   → event_study target = ep["start"]+5min(초·마이크로초 보존)이 어떤 스냅샷 키와도
  --     불일치 → 전부 skip → +5min n=0(실측 2026-11-20 리포트) → _minute_ts() 로 정규화
  -- DB 집계(같은 조회): nav null 102행 · volume 0 1,666행 · 09:00~09:05 510행
  --   → filter_snapshots() 규칙 ①②③ 의 제외 대상(개장 직후 오염 제거)

실측 조회 명령(2026-11-20 실행 — spec_index_backfill §2 연결, DB 127.0.0.1:5434):
  -- SELECT trade_date, close FROM index_daily WHERE index_code='KOSPI'
  --   ORDER BY trade_date DESC LIMIT 3
  --   → 2026-11-20 close 4869.96 · 2026-11-19 close 5137.89 · 2026-11-18 5067.44
  --   → 전일대비 수익률 = (4869.96/5137.89 − 1)×100 = −5.2148% ≤ −1.5% → panic 일
  -- SELECT trade_date, close FROM index_daily WHERE index_code='KOSDAQ'
  --   ORDER BY trade_date DESC LIMIT 3 → 645.68 / 684.81 → −5.714% (panic 일)
  -- SELECT COUNT(*) FROM etf_premium_snapshots WHERE trade_date='2026-11-20' → 39,780행
  -- → --index-code KOSPI 가 주어지면 index_daily 의 전일대비 수익률로 episode 를
  --   panic(≤ −1.5%)/normal 로 나눠 by_regime 을 채운다(스펙 §2). 지수 행이 없으면
  --   지금처럼 null + 사유(0 채움 금지).

계약(스펙 §2 + spec_etf_premium_fix):
  - 입력: DB(없으면 jsonl data/etf_premium/<date>.jsonl). 당일 스냅샷만.
  - by_code: n, 평균·p50·p05·p95, |premium|≥0.5% 체류비율, AR(1) 계수, 반감기(분),
    volume(마지막 스냅샷의 일 누적 quant — 실측: Naver quant 는 당일 누적).
  - episodes: |괴리|≥threshold 연속 구간(시작·끝·최대괴리·지속분). 같은 종목 3분 이내 재발 병합.
  - event_study: 각 episode 시작 가격 대비 +5/+15/+30/+60분 수익률(그 시점 스냅샷 없으면 null)
    → 평균·중앙값·승률·표본수 horizons 별 + 시작 괴리 부호(할인/프리미엄) 분리.
    스냅샷 ts·episode start 는 _minute_ts() 로 분 단위 정규화(spec_etf_premium_fix §1).
  - 필터(spec_etf_premium_fix §2): ① nav null ② volume < --min-volume ③ 개장 후
    --skip-open-min 분 이내 스냅샷 제외(우선순위 순, 첫 해당 규칙에서만 카운트).
    filtered 블록에 제외 사유별 행수 + 필터 전/후 스냅샷·에피소드 수를 남긴다.
  - by_regime(spec_index_backfill §2): --index-code KOSPI 가 주어지면 index_daily 의
    당일 전일대비 수익률 ret 로 episode 를 panic(ret ≤ −1.5%)/normal(그 외)로 나눠
    event study 를 채운다(is_panic_day·panic_threshold_pct 명시). 지수 행(당일·전일)이
    없거나 조회 실패면 regime null + 사유 — 0 채움 금지.
  - 하루 표본(필터 후) < --min-snapshots → 수치 대신 insufficient_sample + 사유(0으로 메우지 않음).
  - 출력 JSON 멱등: 실행 시각 미기록, date 만 키.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
JSONL_DIR = REPO / "data" / "etf_premium"

INDEX_CODES = ("KOSPI", "KOSDAQ", "1001", "2001")
NO_INDEX_REASON = (
    "DB 에 당일 지수 등락 데이터 없음 — 실측 2026-11-18: market_data 는 종목 행만"
    "(지수 코드 0행), macro_indicators 는 금리·환율·물가 9종(지수 없음) → by_regime 미산출(추측 금지)"
)
PANIC_RET_PCT = -1.5   # panic 판정 임계(스펙 index_backfill §2: ret ≤ −1.5%)
# index_daily 경로 SQL 은 테스트가 %s → ? 치환으로 in-memory sqlite 에서 실행한다
# (tests/test_index_backfill.py UPSERT_SQL 과 동일 관례 — DB·네트워크 없이 계약 검증).
INDEX_TODAY_SQL = ("SELECT close FROM index_daily"
                   " WHERE index_code = %s AND trade_date = %s")
INDEX_PREV_SQL = ("SELECT close FROM index_daily WHERE index_code = %s"
                  " AND trade_date < %s ORDER BY trade_date DESC LIMIT 1")

def connect():
    import psycopg2  # 지연 import — 드라이버 없는 환경에서도 순수 함수 import 가능(테스트)
    # .env 소싱 시 컨테이너 좌표(POSTGRES_HOST=postgres)로 덮이는 문제를 dq_claim 과 같은
    # 규칙으로 되돌린다(실측 2026-11-18: "could not translate host name 'postgres'").
    host = os.environ.get("POSTGRES_HOST", "127.0.0.1") or "127.0.0.1"
    port = int(os.environ.get("POSTGRES_PORT", "5434") or 5434)
    if host in ("postgres", "db") and not os.path.exists("/.dockerenv"):
        host = "127.0.0.1"
        port = int(os.environ.get("POSTGRES_HOST_PORT", "5434") or 5434)
    return psycopg2.connect(
        host=host, port=port,
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
        connect_timeout=15)


# ── 순수 계산(테스트 대상) ────────────────────────────────────────────────
def ar1_estimate(values):
    """AR(1) 계수(OLS)와 반감기(분).

    φ = Cov(p_t, p_{t-1}) / Var(p_{t-1}). 반감기 = ln(0.5)/ln(|φ|) — 0<|φ|<1 일 때만,
    |φ|≥1·표본<3·Var=0 이면 (None, None) (스펙 §2.1: φ≥1이면 null).
    """
    if len(values) < 3:
        return None, None
    x, y = values[:-1], values[1:]
    mx = statistics.fmean(x)
    var_x = statistics.fmean([(v - mx) ** 2 for v in x])
    if var_x == 0:
        return None, None
    my = statistics.fmean(y)
    cov = statistics.fmean([(a - mx) * (b - my) for a, b in zip(x, y)])
    phi = cov / var_x
    aphi = abs(phi)
    if 0.0 < aphi < 1.0:
        half = math.log(0.5) / math.log(aphi)
    else:
        half = None
    return phi, half


def build_episodes(points, threshold, merge_gap_min=3):
    """|premium| ≥ threshold 가 유지된 구간(episode) 목록.

    points: [(ts, premium, code, name)] 시간순(같은 종목끼리 정렬돼 있어야 함).
    병합 규칙(스펙 §2.2): 플래그 점 간 간격 ≤ 3분이면 같은 구간 — 연속 유지 중 1분 빠짐까지
    병합되고, 3분 초과 공백이면 새 구간. 시작·끝·최대괴리(부호 유지)·지속분(양끝 포함)·n_points.
    """
    eps, cur = [], None
    gap = timedelta(minutes=merge_gap_min)
    for ts, p, code, name in points:
        if abs(p) < threshold:
            continue
        if cur is not None and cur["code"] == code and (ts - cur["end"]) <= gap:
            cur["end"] = ts
            cur["n_points"] += 1
            if abs(p) > abs(cur["max_premium"]):
                cur["max_premium"] = p
        else:
            if cur is not None:
                eps.append(cur)
            cur = {"code": code, "name": name, "start": ts, "end": ts,
                   "start_premium": p, "max_premium": p, "n_points": 1}
    if cur is not None:
        eps.append(cur)
    for ep in eps:
        ep["duration_min"] = int((ep["end"] - ep["start"]).total_seconds() // 60) + 1
        ep["max_premium"] = round(ep["max_premium"], 4)
        ep["start_premium"] = round(ep["start_premium"], 4)
    return eps


def _percentiles(v):
    """p05, p50, p95 (inclusive 선형 보간). 표본 0 → (None,None,None), 1 → 전부 그 값."""
    n = len(v)
    if n == 0:
        return None, None, None
    if n == 1:
        return v[0], v[0], v[0]
    q = statistics.quantiles(v, n=20, method="inclusive")
    return round(q[0], 4), round(q[9], 4), round(q[18], 4)


def _minute_ts(ts):
    """스냅샷 ts·episode 시작 키를 분 단위 datetime 으로 정규화 — spec_etf_premium_fix §1.

    WHY (2026-11-20 실측): DB etf_premium_snapshots.ts(timestamptz)는 크론 실행 지터로
    같은 분도 초·마이크로초가 다르다(실측 조회: 09:00:02.450947 / 09:01:02.103983 /
    09:05:01.716161 …). event_study 가 target = ep["start"] + timedelta(minutes=h) 로
    시작 ts 의 초·마이크로초를 그대로 보존하면 어떤 스냅샷 키와도 일치하지 않아 전부
    skip 됐다(+5min n=0, 실측 2026-11-20 리포트). 분 단위로 절사한 키를 양쪽(키 사전·
    조회)에 쓰면 ±2분 오프셋 탐색이 실제 행에 닿는다. datetime 이 아니면(예: ISO 문자열)
    그대로 둔다 — 양쪽이 같은 형식이기만 하면 된다.
    """
    if isinstance(ts, datetime):
        return ts.replace(second=0, microsecond=0)
    return ts


def event_study(episodes, snaps, horizons, tolerance_min=2):
    """각 episode 시작 가격 대비 +h분 가격 수익률(%) → horizons 별 평균·중앙값·승률·표본수.

    '그 시점 스냅샷' = 시작+h 분에서 ±2분 이내 가장 가까운 스냅샷(1분 단위 크론이 특정 분을
    놓치면 전부 null 이 되는 것을 막는 허용 — 2026-11-18 설계 결정). 없으면 null(0 채움 금지).
    키는 _minute_ts() 로 분 단위 정규화(초·마이크로초 지터 제거 — spec_etf_premium_fix §1).
    반환: {f"+{h}min": {"n","mean_pct","median_pct","win_rate",
                        "by_start_sign": {"discount":..., "premium":...}}, ...}
    """
    price = {(r["code"], _minute_ts(r["ts"])): r["price"]
             for r in snaps if r.get("price") is not None}
    rets = {h: [] for h in horizons}
    rets_sign = {h: {"discount": [], "premium": []} for h in horizons}
    for ep in episodes:
        code = ep["code"]
        p0 = price.get((code, _minute_ts(ep["start"])))
        if p0 is None:
            continue
        sign = "discount" if ep["start_premium"] < 0 else "premium"
        for h in horizons:
            target = _minute_ts(ep["start"]) + timedelta(minutes=h)
            p = None
            for off in (0, -1, 1, -2, 2):            # 가까운 스냅샷 우선
                if off < 0 and off < -tolerance_min:
                    continue
                cand = price.get((code, target + timedelta(minutes=off)))
                if cand is not None:
                    p = cand
                    break
            if p is None:
                continue
            r = round((p / p0 - 1.0) * 100.0, 4)
            rets[h].append(r)
            rets_sign[h][sign].append(r)
    out = {}
    for h in horizons:
        out[f"+{h}min"] = _summarize(rets[h])
        out[f"+{h}min"]["by_start_sign"] = {
            "discount": _summarize(rets_sign[h]["discount"]),
            "premium": _summarize(rets_sign[h]["premium"]),
        }
    return out


def _summarize(rets):
    """수익률 리스트 요약. 표본 0 → 평균·중앙값·승률 None(0 채움 금지), n 만 0."""
    if not rets:
        return {"n": 0, "mean_pct": None, "median_pct": None, "win_rate": None}
    return {"n": len(rets),
            "mean_pct": round(statistics.fmean(rets), 4),
            "median_pct": round(statistics.median(rets), 4),
            "win_rate": round(sum(1 for r in rets if r > 0) / len(rets), 4)}


def filter_snapshots(snaps, min_volume=1, skip_open_min=5, stale_minutes=3, min_amount=1):
    """유효 스냅샷 필터 — docs/spec_etf_premium_fix.md §2 (오염 제거).

    규칙(우선순위 순 — 각 행은 처음 해당하는 규칙에서만 카운트, 합계 = 제외 행수):
      ① nav 가 null → dropped_no_nav
      ② volume 이 null 이거나 < min_volume → dropped_no_volume
      ②-1 amount 가 있는데 < min_amount → dropped_no_amount
         (실측 2026-11-20 0194R0: 09:00~09:1x price 3,555 고정·amount 0 — 체결대금 0 인
          행은 '가격이 살아있지 않다'는 직접 증거다. volume=1 같은 플레이스홀더를 걸러낸다)
      ③ 개장(09:00) 후 skip_open_min 분 미만(09:00 ≤ t < 09:05) → dropped_open_window
      ④ 직전 stale_minutes 개 스냅샷과 **가격이 완전히 동일**(1분 연속) → dropped_stale
         (spec_etf_stale_fix.md — 0194R0 +15.76% 처럼 가격이 전일 종가에 멈춘 채
          volume 만 1 이상으로 찍힌 행을 걷어낸다)

    WHY (2026-11-20 실측, 조회: data/etf_premium/2026-11-20.jsonl 09:02 행):
      0193T0 KODEX SK하이닉스단일종목레버리지 price 4,255(전일 종가 유지 — 개장 직후
      미체결이라 nowVal 이 전일 종가) vs nav 3,683 → premium +15.5308%, volume 0.
      체결이 시작된 09:04 이후 정상화 — 개장 직후 미체결(volume 0) 행이 만든 가짜 괴리.
      DB 집계(같은 날): nav null 102행 · volume 0 1,666행 · 09:00~09:05 510행.
      반환: (valid, {dropped_no_nav, dropped_no_volume, dropped_open_window}, 사유문).
    """
    valid, dropped = [], {"dropped_no_nav": 0, "dropped_no_volume": 0,
                          "dropped_open_window": 0, "dropped_stale": 0,
                          "dropped_no_amount": 0}
    for r in snaps:
        if r.get("nav") is None:
            dropped["dropped_no_nav"] += 1
            continue
        vol = r.get("volume")
        if vol is None or vol < min_volume:
            dropped["dropped_no_volume"] += 1
            continue
        amt = r.get("amount")
        if amt is not None and amt < min_amount:
            dropped["dropped_no_amount"] += 1
            continue
        t = r["ts"]
        m = (t - t.replace(hour=9, minute=0, second=0, microsecond=0)).total_seconds() / 60.0
        if 0.0 <= m < skip_open_min:
            dropped["dropped_open_window"] += 1
            continue
        valid.append(r)

    # ④ 정지 가격(stale) 제거 — 같은 종목에서 직전 stale_minutes 스냅샷과 가격이 연속 동일.
    if stale_minutes and stale_minutes > 0:
        by_code = {}
        for r in valid:
            by_code.setdefault(r["code"], []).append(r)
        keep = []
        for code, rows in by_code.items():
            rows.sort(key=lambda r: r["ts"])
            prices = [r.get("price") for r in rows]
            for i, r in enumerate(rows):
                if i < stale_minutes:
                    keep.append(r)
                    continue
                window = prices[i - stale_minutes:i + 1]
                same = window[0] is not None and all(p == window[0] for p in window)
                consecutive = (r["ts"] - rows[i - stale_minutes]["ts"]).total_seconds() <= 60 * stale_minutes + 30
                # WHY nav 이동 조건(2026-11-20 실측 보정): 비유동 ETF 는 체결이 없어 가격이
                # 정상적으로 몇 분씩 고정된다. 가격 정지만으로 자르면 전체의 43%(17,163행)가
                # 날아가고 정작 아티팩트(0194R0 +13.8%)는 남았다. 그래서 **NAV 가 같은 창에서
                # 0.3% 이상 움직였는데 가격만 멈춘** 경우로 한정한다(진짜 미체결 고정 가격).
                navs = [x.get("nav") for x in rows[i - stale_minutes:i + 1]]
                nav_moved = (navs[0] not in (None, 0) and all(v not in (None, 0) for v in navs)
                             and abs(navs[-1] / navs[0] - 1.0) >= 0.003)
                if same and consecutive and nav_moved:
                    dropped["dropped_stale"] += 1
                else:
                    keep.append(r)
        keep_ids = {id(r) for r in keep}
        valid = [r for r in valid if id(r) in keep_ids]

    note = (f"min_volume={min_volume} min_amount={min_amount} skip_open_min={skip_open_min} stale_minutes={stale_minutes}:"
            f" no_nav={dropped['dropped_no_nav']}"
            f" no_volume={dropped['dropped_no_volume']}"
            f" open={dropped['dropped_open_window']}"
            f" stale={dropped['dropped_stale']}"
            f" no_amount={dropped['dropped_no_amount']}")
    return valid, dropped, note


def _points(snaps):
    """build_episodes 입력 포맷 — 코드별 시간순, premium_pct 결측 제외."""
    return [(r["ts"], r["premium_pct"], r["code"], r["name"])
            for r in sorted(snaps, key=lambda r: (r["code"], r["ts"]))
            if r["premium_pct"] is not None]


# ── 입력 로드 ─────────────────────────────────────────────────────────────
def load_snapshots(date_s: str, jsonl_dir: str):
    """DB(있으면) → jsonl 폴백. 반환: (snaps, source_note).

    snaps = [{"ts": datetime, "code","name","price","nav","premium_pct","volume"}, ...]
    """
    snaps, note_db = [], None
    try:
        conn = connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT ts, code, name, price, nav, premium_pct, volume, amount"
                    " FROM etf_premium_snapshots WHERE trade_date = %s"
                    " ORDER BY ts, code", (date_s,))
                for ts, code, name, price, nav, prem, vol, amt in cur.fetchall():
                    # numeric 컬럼은 Decimal 로 온다 → float 정규화(실측 2026-11-18:
                    # Decimal 그대로 두면 statistics·round 후 JSON 직렬화 TypeError).
                    # timestamptz 는 UTC 로 온다 → 로컬(KST)로 정규화해 jsonl 경로 출력과
                    # 동일하게 한다(입력원이 달라도 산출이 흔들리지 않게 — 멱등).
                    snaps.append({"ts": ts.astimezone(), "code": code, "name": name,
                                  "price": float(price) if price is not None else None,
                                  "nav": float(nav) if nav is not None else None,
                                  "premium_pct": float(prem) if prem is not None else None,
                                  "volume": vol,
                                  "amount": float(amt) if amt is not None else None})
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 — jsonl 폴백
        note_db = f"DB 조회 실패({type(e).__name__})"
    if snaps:
        return snaps, f"DB etf_premium_snapshots {len(snaps)}행"
    path = Path(jsonl_dir) / f"{date_s}.jsonl"
    jsnaps = []
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    d = json.loads(line)
                    if d.get("trade_date") != date_s:
                        continue
                    ts = datetime.fromisoformat(d["ts"])
                    for r in d.get("rows", []):
                        jsnaps.append({"ts": ts, "code": r["code"], "name": r["name"],
                                       "price": r.get("price"), "nav": r.get("nav"),
                                       "premium_pct": r.get("premium_pct"),
                                       "volume": r.get("volume"),
                                       "amount": r.get("amount")})
        except (OSError, ValueError):
            pass
    if jsnaps:
        return jsnaps, f"jsonl {path} {len(jsnaps)}행"
    note = note_db or "DB·jsonl 모두 0행"
    return [], note


def load_index_change(conn, date_s: str, index_code=None):
    """당일 지수 등락(%) (직전 거래일 종가 대비) → (regime_dict, 사유).

    index_code 가 주어지면(spec_index_backfill §2) index_daily 에서
    ret = close(date_s)/close(직전 거래일) − 1 을 읽는다. 당일 행·전일 행이 없거나
    조회 실패면 (None, 사유) — 0 채움 금지.
    index_code 가 없으면 기존 동작: market_data 종목 코드 프로브
    (실측 2026-11-18: 지수 행 0개 → None + NO_INDEX_REASON).
    """
    if conn is None:
        return None, NO_INDEX_REASON
    if index_code:
        try:
            cur = conn.cursor()   # with 문 금지 — sqlite3.Cursor 는 컨텍스트 매니저 미지원
            try:                  # (실측 TypeError — in-memory 계약 테스트 경로)
                cur.execute(INDEX_TODAY_SQL, (index_code, date_s))
                row = cur.fetchone()
                if row is None or row[0] is None:
                    return None, (f"index_daily 에 {index_code} {date_s} 행 없음"
                                  f" → by_regime 미산출(0 채움 금지)")
                cur.execute(INDEX_PREV_SQL, (index_code, date_s))
                prev = cur.fetchone()
                if prev is None or not prev[0]:
                    return None, (f"index_daily 에 {index_code} {date_s} 직전"
                                  f" 거래일 행 없음 → by_regime 미산출(0 채움 금지)")
                return ({index_code: round((float(row[0]) / float(prev[0]) - 1.0)
                                           * 100.0, 4)}, "")
            finally:
                cur.close()
        except Exception as exc:  # noqa: BLE001 — 지수 없음 취급(null + 사유)
            return None, (f"index_daily 조회 실패({type(exc).__name__}) →"
                          f" by_regime 미산출(0 채움 금지)")
    try:
        out = {}
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT stock_code, close_price FROM market_data"
                " WHERE trade_date = %s AND stock_code = ANY(%s)",
                (date_s, list(INDEX_CODES)))
            for code, close in cur.fetchall():
                cur.execute(
                    "SELECT close_price FROM market_data WHERE stock_code = %s"
                    " AND trade_date < %s ORDER BY trade_date DESC LIMIT 1",
                    (code, date_s))
                prev = cur.fetchone()
                if close is not None and prev and prev[0]:
                    out[code] = round((float(close) / float(prev[0]) - 1.0) * 100.0, 4)
        finally:
            cur.close()
    except Exception:  # noqa: BLE001 — 지수 없음 취급(null + 사유)
        return None, NO_INDEX_REASON
    return (out or None), NO_INDEX_REASON


# ── 보고서 조립 ───────────────────────────────────────────────────────────
def build_report(date_s, snaps, *, min_snapshots, threshold, horizons,
                 regime=None, regime_reason=None, source_note="",
                 min_volume=1, skip_open_min=5, stale_minutes=3, min_amount=1):
    """당일 스냅샷 → 분석 보고 dict. 표본 부족이면 insufficient_sample 만(수치 없음 — 스펙 §2).

    필터(spec_etf_premium_fix §2)를 먼저 적용하고, filtered 블록에 제외 사유별 행수와
    필터 전/후 스냅샷·에피소드 수를 남긴다(필터 사유는 리포터 상단에 표기).
    """
    valid, dropped, filter_note = filter_snapshots(snaps, min_volume, skip_open_min,
                                                   stale_minutes, min_amount)
    eps_before = len(build_episodes(_points(snaps), threshold))
    ts_set = sorted({r["ts"] for r in valid})
    n_snaps = len(ts_set)
    codes = sorted({r["code"] for r in valid})
    eps = build_episodes(_points(valid), threshold)
    filtered = {"min_volume": min_volume, "skip_open_min": skip_open_min,
                "stale_minutes": stale_minutes, "min_amount": min_amount,
                "n_snaps_before": len(snaps), "n_snaps_after": len(valid),
                "episodes_before": eps_before, "episodes_after": len(eps),
                "dropped_no_nav": dropped["dropped_no_nav"],
                "dropped_no_volume": dropped["dropped_no_volume"],
                "dropped_stale": dropped["dropped_stale"],
                "dropped_no_amount": dropped["dropped_no_amount"],
                "dropped_open_window": dropped["dropped_open_window"],
                "reason": filter_note}
    if n_snaps < min_snapshots:
        reason = (f"당일 데이터 없음({source_note})" if n_snaps == 0 else
                  f"당일 스냅샷 {n_snaps}개 < {min_snapshots}(--min-snapshots) — 수치 산출 보류")
        if len(valid) < len(snaps):
            reason += f"(필터 제외 {len(snaps) - len(valid)}행: {filter_note})"
        return {"date": date_s, "filtered": filtered,
                "insufficient_sample": {"min_snapshots": min_snapshots,
                                        "n_snapshots": n_snaps,
                                        "n_codes": len(codes),
                                        "reason": reason + "(0으로 메우지 않음)"}}

    by_code = {}
    for code in codes:
        rows = sorted((r for r in valid if r["code"] == code), key=lambda r: r["ts"])
        v = [r["premium_pct"] for r in rows if r["premium_pct"] is not None]
        p05, p50, p95 = _percentiles(v)
        phi, half = ar1_estimate(v)
        occupancy = (sum(1 for x in v if abs(x) >= 0.5) / len(v)) if v else None
        by_code[code] = {
            "name": rows[0]["name"],
            "n": len(rows),
            "premium_mean": round(statistics.fmean(v), 4) if v else None,
            "premium_p50": p50, "premium_p05": p05, "premium_p95": p95,
            "occupancy_abs_ge_05_pct": round(occupancy, 4) if occupancy is not None else None,
            "ar1_phi": round(phi, 4) if phi is not None else None,
            "half_life_min": round(half, 2) if half is not None else None,
            "volume_last_cum": rows[-1]["volume"],   # Naver quant = 당일 누적(실측)
        }

    ev = event_study(eps, valid, horizons)

    if regime is None:
        by_regime = {"regime": None, "reason": regime_reason or NO_INDEX_REASON}
    else:
        # spec_index_backfill §2: panic = 전일대비 수익률 ≤ −1.5%, normal = 그 외
        rets = [v for v in regime.values() if v is not None]
        panic_day = any(r <= PANIC_RET_PCT for r in rets)
        panic_eps, normal_eps = [], []
        for ep in eps:
            (panic_eps if panic_day else normal_eps).append(ep)
        by_regime = {"regime": "available", "index_pct": regime,
                     "panic_threshold_pct": PANIC_RET_PCT,
                     "is_panic_day": panic_day,
                     "panic": event_study(panic_eps, valid, horizons),
                     "normal": event_study(normal_eps, valid, horizons)}

    eps_out = []
    for ep in eps:
        e = dict(ep)
        e["start"] = ep["start"].isoformat(timespec="seconds")
        e["end"] = ep["end"].isoformat(timespec="seconds")
        eps_out.append(e)

    return {"date": date_s, "n_snapshots": n_snaps, "n_codes": len(codes),
            "filtered": filtered, "by_code": by_code, "episodes": eps_out,
            "event_study": ev, "by_regime": by_regime}


def _filter_line(f) -> str:
    """필터 사유 상단 표기 — spec_etf_premium_fix §2(리포트 상단 + 전/후 에피소드 수)."""
    return (f"- **필터(spec_etf_premium_fix §2)**: nav null · volume<{f['min_volume']} ·"
            f" 개장 {f['skip_open_min']}분 이내 스냅샷 제외 —"
            f" 제외 {f['dropped_no_nav']}+{f['dropped_no_volume']}"
            f"+{f['dropped_open_window']}행 · 스냅샷 {f['n_snaps_before']}"
            f"→{f['n_snaps_after']} · 에피소드 {f['episodes_before']}"
            f"→{f['episodes_after']}건")


def report_to_md(rep) -> str:
    """동일 입력에 동일 출력(멱등) — 정렬 고정, 실행 시각 미기록."""
    lines = [f"# ETF 프리미엄 모니터 — {rep['date']}", ""]
    if "insufficient_sample" in rep:
        ins = rep["insufficient_sample"]
        lines.append(f"- **insufficient_sample**: {ins['reason']}"
                     f" (min_snapshots={ins['min_snapshots']},"
                     f" n_snapshots={ins['n_snapshots']}, n_codes={ins['n_codes']})")
        if rep.get("filtered"):
            lines.append(_filter_line(rep["filtered"]))
        return "\n".join(lines) + "\n"
    lines.append(f"- 스냅샷 {rep['n_snapshots']}회 · 종목 {rep['n_codes']}개"
                 f" · episode {len(rep['episodes'])}건 · by_regime: "
                 + (str(rep['by_regime'].get('regime'))
                    if rep['by_regime'].get('regime') else 'null(지수 데이터 없음)'))
    lines.append(_filter_line(rep["filtered"]))
    lines += ["", "## by_code (체류비율 상위 20)", "",
              "| code | name | n | mean | p50 | p05 | p95 | 체류≥0.5% | φ | 반감기(분) |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    top = sorted(rep["by_code"].items(),
                 key=lambda kv: (-(kv[1]["occupancy_abs_ge_05_pct"] or -1), kv[0]))[:20]
    for code, st in top:
        lines.append(f"| {code} | {st['name']} | {st['n']} | {st['premium_mean']}"
                     f" | {st['premium_p50']} | {st['premium_p05']} | {st['premium_p95']}"
                     f" | {st['occupancy_abs_ge_05_pct']} | {st['ar1_phi']}"
                     f" | {st['half_life_min']} |")
    lines += ["", "## episodes (최대괴리 상위 10)", "",
              "| code | name | 시작 | 끝 | 최대괴리% | 지속분 |", "|---|---|---|---|---|---|"]
    for ep in sorted(rep["episodes"], key=lambda e: -abs(e["max_premium"]))[:10]:
        lines.append(f"| {ep['code']} | {ep['name']} | {ep['start']} | {ep['end']}"
                     f" | {ep['max_premium']:+.2f} | {ep['duration_min']} |")
    lines += ["", "## event_study (episode 시작 대비)", "",
              "| horizon | n | 평균% | 중앙값% | 승률 | 디스카운트n | 디스카운트평균% |", "|---|---|---|---|---|---|---|"]
    for h, s in rep["event_study"].items():
        d = s["by_start_sign"]["discount"]
        lines.append(f"| {h} | {s['n']} | {s['mean_pct']} | {s['median_pct']}"
                     f" | {s['win_rate']} | {d['n']} | {d['mean_pct']} |")
    if rep["by_regime"].get("regime"):
        br = rep["by_regime"]
        idx = ", ".join(f"{k}={v}" for k, v in br["index_pct"].items())
        lines += ["", f"## by_regime — panic = 전일대비 ≤ {br['panic_threshold_pct']}%",
                  "", f"- index_pct: {idx} · is_panic_day: {br['is_panic_day']}", "",
                  "| regime | horizon | n | 평균% | 중앙값% | 승률 |", "|---|---|---|---|---|---|"]
        for regime_lbl in ("panic", "normal"):
            for h, s in br[regime_lbl].items():
                lines.append(f"| {regime_lbl} | {h} | {s['n']} | {s['mean_pct']}"
                             f" | {s['median_pct']} | {s['win_rate']} |")
    elif rep["by_regime"].get("reason"):
        lines += ["", f"by_regime: {rep['by_regime']['reason']}"]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="ETF 괴리율 당일 분석(스펙 §2)")
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"),
                    help="분석일 YYYY-MM-DD(기본 오늘)")
    ap.add_argument("--min-snapshots", type=int, default=60,
                    help="미만이면 insufficient_sample(기본 60)")
    ap.add_argument("--premium-threshold", type=float, default=0.5,
                    help="episode 판정 |괴리|%% 임계(기본 0.5)")
    ap.add_argument("--horizons", default="5,15,30,60",
                    help="event study 분(쉼표 구분, 기본 5,15,30,60)")
    ap.add_argument("--min-volume", type=int, default=1,
                    help="volume 미만 스냅샷 제외(기본 1 — spec_etf_premium_fix §2)")
    ap.add_argument("--min-amount", type=int, default=1,
                    help="체결대금(amount) 하한 — 0/플레이스홀더 행 제외(기본 1)")
    ap.add_argument("--stale-minutes", type=int, default=3,
                    help="직전 N분 가격 무변동 스냅샷 제외(0=해제, 기본 3)")
    ap.add_argument("--skip-open-min", type=int, default=5,
                    help="개장 후 이 시간(분) 이내 스냅샷 제외(기본 5 — spec_etf_premium_fix §2)")
    ap.add_argument("--index-code", default=None,
                    help="index_daily 지수 코드(예: KOSPI) — 전일대비 수익률로 episode 를"
                         " panic(≤-1.5%%)/normal 로 분류해 by_regime 을 채운다"
                         " (spec_index_backfill §2. 미지정이면 기존 market_data 프로브)")
    ap.add_argument("--json-out", default=str(REPO / "data/reports/etf_premium.json"))
    ap.add_argument("--md-out", default=str(REPO / "data/reports/etf_premium.md"))
    args = ap.parse_args(argv)

    horizons = [int(h) for h in args.horizons.split(",") if h.strip()]
    snaps, note = load_snapshots(args.date, str(JSONL_DIR))
    regime, regime_reason = None, None
    if snaps:
        conn = connect()
        try:
            regime, regime_reason = load_index_change(conn, args.date, args.index_code)
        finally:
            conn.close()
    rep = build_report(args.date, snaps, min_snapshots=args.min_snapshots,
                       threshold=args.premium_threshold, horizons=horizons,
                       regime=regime, regime_reason=regime_reason, source_note=note,
                       min_volume=args.min_volume, skip_open_min=args.skip_open_min,
                       stale_minutes=args.stale_minutes, min_amount=args.min_amount)

    out = Path(args.json_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(rep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, out)
    md = Path(args.md_out)
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_text(report_to_md(rep), encoding="utf-8")
    if "insufficient_sample" in rep:
        print(f"[etf_premium] date={args.date} insufficient_sample"
              f" (n_snapshots={rep['insufficient_sample']['n_snapshots']})", flush=True)
        claimed = 0
    else:
        f = rep["filtered"]
        claimed = f["n_snaps_after"]
        print(f"[etf_premium] date={args.date} snaps={rep['n_snapshots']}"
              f" codes={rep['n_codes']} episodes={len(rep['episodes'])}"
              f" filtered={f['n_snaps_after']}/{f['n_snaps_before']}"
              f" episodes_before={f['episodes_before']}", flush=True)
        if rep["by_regime"].get("regime"):
            br = rep["by_regime"]
            print(f"  by_regime: {br['index_pct']} is_panic_day={br['is_panic_day']}"
                  f" panic_n={br['panic']['+' + str(horizons[0]) + 'min']['n']}"
                  f" normal_n={br['normal']['+' + str(horizons[0]) + 'min']['n']}"
                  f" (threshold {br['panic_threshold_pct']}%)", flush=True)
    # 자기신고 3분리(소스 수신/파서 생성/실제 저장) — DB 읽기 전용 분석이라
    # dq_claim.record_claim 은 쓰지 않는다(money_scoreboard 관례: 출력에 내장).
    # 재실행 시 같은 날짜 파일을 덮어쓴다(멱등 — 실행 시각 미기록).
    print(f"[claim] etf_premium_analysis source={len(snaps)} claimed={claimed}"
          f" persisted=2 (json+md 리포트)", flush=True)
    print(f"  -> {args.json_out} / {args.md_out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
