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

계약(스펙 §2):
  - 입력: DB(없으면 jsonl data/etf_premium/<date>.jsonl). 당일 스냅샷만.
  - by_code: n, 평균·p50·p05·p95, |premium|≥0.5% 체류비율, AR(1) 계수, 반감기(분),
    volume(마지막 스냅샷의 일 누적 quant — 실측: Naver quant 는 당일 누적).
  - episodes: |괴리|≥threshold 연속 구간(시작·끝·최대괴리·지속분). 같은 종목 3분 이내 재발 병합.
  - event_study: 각 episode 시작 가격 대비 +5/+15/+30/+60분 수익률(그 시점 스냅샷 없으면 null)
    → 평균·중앙값·승률·표본수 horizons 별 + 시작 괴리 부호(할인/프리미엄) 분리.
  - 하루 표본 < --min-snapshots → 수치 대신 insufficient_sample + 사유(0으로 메우지 않음).
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


def event_study(episodes, snaps, horizons, tolerance_min=2):
    """각 episode 시작 가격 대비 +h분 가격 수익률(%) → horizons 별 평균·중앙값·승률·표본수.

    '그 시점 스냅샷' = 시작+h 분에서 ±2분 이내 가장 가까운 스냅샷(1분 단위 크론이 특정 분을
    놓치면 전부 null 이 되는 것을 막는 허용 — 2026-11-18 설계 결정). 없으면 null(0 채움 금지).
    반환: {f"+{h}min": {"n","mean_pct","median_pct","win_rate",
                        "by_start_sign": {"discount":..., "premium":...}}, ...}
    """
    price = {(r["code"], r["ts"]): r["price"]
             for r in snaps if r.get("price") is not None}
    rets = {h: [] for h in horizons}
    rets_sign = {h: {"discount": [], "premium": []} for h in horizons}
    for ep in episodes:
        code = ep["code"]
        p0 = price.get((code, ep["start"]))
        if p0 is None:
            continue
        sign = "discount" if ep["start_premium"] < 0 else "premium"
        for h in horizons:
            target = ep["start"] + timedelta(minutes=h)
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
                    "SELECT ts, code, name, price, nav, premium_pct, volume"
                    " FROM etf_premium_snapshots WHERE trade_date = %s"
                    " ORDER BY ts, code", (date_s,))
                for ts, code, name, price, nav, prem, vol in cur.fetchall():
                    # numeric 컬럼은 Decimal 로 온다 → float 정규화(실측 2026-11-18:
                    # Decimal 그대로 두면 statistics·round 후 JSON 직렬화 TypeError).
                    # timestamptz 는 UTC 로 온다 → 로컬(KST)로 정규화해 jsonl 경로 출력과
                    # 동일하게 한다(입력원이 달라도 산출이 흔들리지 않게 — 멱등).
                    snaps.append({"ts": ts.astimezone(), "code": code, "name": name,
                                  "price": float(price) if price is not None else None,
                                  "nav": float(nav) if nav is not None else None,
                                  "premium_pct": float(prem) if prem is not None else None,
                                  "volume": vol})
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
                                       "volume": r.get("volume")})
        except (OSError, ValueError):
            pass
    if jsnaps:
        return jsnaps, f"jsonl {path} {len(jsnaps)}행"
    note = note_db or "DB·jsonl 모두 0행"
    return [], note


def load_index_change(conn, date_s: str):
    """당일 KOSPI/KOSDAQ 지수 등락(%) (직전 거래일 종가 대비). 없으면 None(실측: 지수 데이터 없음)."""
    if conn is None:
        return None
    try:
        out = {}
        with conn.cursor() as cur:
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
    except Exception:  # noqa: BLE001 — 지수 없음 취급(null + 사유)
        return None
    return out or None


# ── 보고서 조립 ───────────────────────────────────────────────────────────
def build_report(date_s, snaps, *, min_snapshots, threshold, horizons,
                 regime=None, source_note=""):
    """당일 스냅샷 → 분석 보고 dict. 표본 부족이면 insufficient_sample 만(수치 없음 — 스펙 §2)."""
    ts_set = sorted({r["ts"] for r in snaps})
    n_snaps = len(ts_set)
    codes = sorted({r["code"] for r in snaps})
    if n_snaps < min_snapshots:
        reason = (f"당일 데이터 없음({source_note})" if n_snaps == 0 else
                  f"당일 스냅샷 {n_snaps}개 < {min_snapshots}(--min-snapshots) — 수치 산출 보류")
        return {"date": date_s,
                "insufficient_sample": {"min_snapshots": min_snapshots,
                                        "n_snapshots": n_snaps,
                                        "n_codes": len(codes),
                                        "reason": reason + "(0으로 메우지 않음)"}}

    by_code = {}
    for code in codes:
        rows = sorted((r for r in snaps if r["code"] == code), key=lambda r: r["ts"])
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

    points = [(r["ts"], r["premium_pct"], r["code"], r["name"])
              for r in sorted(snaps, key=lambda r: (r["code"], r["ts"]))
              if r["premium_pct"] is not None]
    eps = build_episodes(points, threshold)
    ev = event_study(eps, snaps, horizons)

    if regime is None:
        by_regime = {"regime": None, "reason": NO_INDEX_REASON}
    else:
        panic_eps, normal_eps = [], []
        for ep in eps:
            big = any(v is not None and abs(v) >= 1.5 for v in regime.values())
            (panic_eps if big else normal_eps).append(ep)
        by_regime = {"regime": "available", "index_pct": regime,
                     "panic": event_study(panic_eps, snaps, horizons),
                     "normal": event_study(normal_eps, snaps, horizons)}

    eps_out = []
    for ep in eps:
        e = dict(ep)
        e["start"] = ep["start"].isoformat(timespec="seconds")
        e["end"] = ep["end"].isoformat(timespec="seconds")
        eps_out.append(e)

    return {"date": date_s, "n_snapshots": n_snaps, "n_codes": len(codes),
            "by_code": by_code, "episodes": eps_out, "event_study": ev,
            "by_regime": by_regime}


def report_to_md(rep) -> str:
    """동일 입력에 동일 출력(멱등) — 정렬 고정, 실행 시각 미기록."""
    lines = [f"# ETF 프리미엄 모니터 — {rep['date']}", ""]
    if "insufficient_sample" in rep:
        ins = rep["insufficient_sample"]
        lines.append(f"- **insufficient_sample**: {ins['reason']}"
                     f" (min_snapshots={ins['min_snapshots']},"
                     f" n_snapshots={ins['n_snapshots']}, n_codes={ins['n_codes']})")
        return "\n".join(lines) + "\n"
    lines.append(f"- 스냅샷 {rep['n_snapshots']}회 · 종목 {rep['n_codes']}개"
                 f" · episode {len(rep['episodes'])}건 · by_regime: "
                 + (str(rep['by_regime'].get('regime'))
                    if rep['by_regime'].get('regime') else 'null(지수 데이터 없음)'))
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
    if rep["by_regime"].get("reason"):
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
    ap.add_argument("--json-out", default=str(REPO / "data/reports/etf_premium.json"))
    ap.add_argument("--md-out", default=str(REPO / "data/reports/etf_premium.md"))
    args = ap.parse_args(argv)

    horizons = [int(h) for h in args.horizons.split(",") if h.strip()]
    snaps, note = load_snapshots(args.date, str(JSONL_DIR))
    regime = load_index_change(connect(), args.date) if snaps else None
    rep = build_report(args.date, snaps, min_snapshots=args.min_snapshots,
                       threshold=args.premium_threshold, horizons=horizons,
                       regime=regime, source_note=note)

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
    else:
        print(f"[etf_premium] date={args.date} snaps={rep['n_snapshots']}"
              f" codes={rep['n_codes']} episodes={len(rep['episodes'])}", flush=True)
    print(f"  -> {args.json_out} / {args.md_out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
