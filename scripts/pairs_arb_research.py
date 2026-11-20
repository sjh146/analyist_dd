#!/usr/bin/env python3
"""쌍둥이(pairs) 매매 실측 하네스 — 하락장 조건 포함.

WHY(2026-11-19 사용자 요구 "차익거래·쌍둥이 매매로 하락장에서도 돈을 벌 수 있는지 테스트"):
  1) 우선주/보통주(36쌍 실측)와 동일기초 ETF 쌍의 **스프레드 평균회귀**를 룰로 백테스트한다.
  2) 결과를 **진입 시장 레짐(down/up)** 으로 분리해, 하락장에서 기대값이 (+)인지 판정한다.
  3) 두 모드를 모두 낸다:
     · both     = 비싼다리 매도(이론값, 개인 공매도 제약 무시)
     · long-only= 싼다리만 매수(개인 제약 반영)

데이터 한계(실측): DB 에 **지수 데이터가 없다**(market_data 는 종목만) → 시장 프록시로
전 종목 일별 수익률 **중앙값**의 누적(breadth)을 쓴다. 추정 지수를 만들지 않는다.
ETF NAV 괴리 이력은 2026-11-19부터만 축적 중이라 이 하네스 표본에 넣지 않는다.

--use-index (2026-11-20, spec_index_backfill §2 연결): index_daily 백필 이후에는
시장 프록시를 **실지수**로 대체할 수 있다. 실측 조회(DB 127.0.0.1:5434, 프로세스 환경변수):
  -- SELECT index_code, COUNT(*), MIN(trade_date), MAX(trade_date) FROM index_daily
  --   GROUP BY index_code → KOSPI 459행 · KOSDAQ 459행 (2025-01-02 ~ 2026-11-20)
  -- SELECT trade_date, close FROM index_daily WHERE index_code='KOSPI'
  --   ORDER BY trade_date DESC LIMIT 2 → 2026-11-20 close 4869.96 (전일 5137.89)
  --   → 전일대비 −5.2148% (하락 레짐 판정 입력이 되는 실측)
breadth(2025-08-01~ market_data)와 기간이 다르므로 기본은 기존 breadth 유지 —
--use-index KOSPI|KOSDAQ 를 줄 때만 index_daily 종가를 프록시로 쓴다.

사용:
  /usr/bin/python3 scripts/pairs_arb_research.py --mode both
  /usr/bin/python3 scripts/pairs_arb_research.py --mode long-only
  /usr/bin/python3 scripts/pairs_arb_research.py --mode both --use-index KOSPI
"""
from __future__ import annotations

import argparse
import json
import os
import re
from collections import defaultdict

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRANDS = ("KODEX", "TIGER", "ARIRANG", "KB", "한투", "SOL", "ACE", "RISE", "PLUS", "TIMEFOLIO",
          "BNK", "히어로즈", "마이다스", "파워", "삼성", "미래에셋", "한화", "신한", "우리")
PREF_RE = re.compile(r"^(.*?)(2우B|3우B|우B|우C|우)$")


def connect():
    import psycopg2

    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.environ.get("POSTGRES_PORT", "5434")),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )


def load(db):
    cur = db.cursor()
    cur.execute("""
        SELECT m.trade_date::text, m.stock_code, m.close_price::float8, m.trading_value::float8
        FROM market_data m
        WHERE m.close_price > 0 AND m.trade_date >= '2025-08-01'
    """)
    px, tv, n_src = {}, {}, 0
    for d, code, close, value in cur.fetchall():
        n_src += 1
        px.setdefault(code, {})[d] = float(close)
        tv.setdefault(code, {})[d] = float(value or 0)
    cur.execute("SELECT stock_code, stock_name, COALESCE(instrument_type,'STOCK') FROM stocks")
    meta = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
    cur.close()
    return px, tv, meta, n_src


INDEX_SERIES_SQL = ("SELECT trade_date, close FROM index_daily"
                    " WHERE index_code = %s AND close > 0 ORDER BY trade_date")


def load_index_series(conn, index_code):
    """index_daily 종가 시계열 → {date_str: close} — breadth_index 와 같은 모양(프록시 대체).

    spec_index_backfill §2(--use-index): 시장 프록시를 실지수로 대체한다.
    키는 str(trade_date) 로 텍스트 정규화(breadth_index 의 px 키 = market_data
    trade_date::text 와 동일 형식이라 regime_map·simulate 결합이 그대로 동작).
    행이 없거나 조회 실패면 빈 dict(regime 은 전 구간 null — 0 채움 금지).
    """
    if conn is None:
        return {}
    try:
        cur = conn.cursor()   # with 문 금지 — sqlite3.Cursor 는 컨텍스트 매니저 미지원
        try:                  # (실측 TypeError — in-memory 계약 테스트 경로)
            cur.execute(INDEX_SERIES_SQL, (index_code,))
            out = {}
            for d, close in cur.fetchall():
                if close is None:
                    continue
                out[str(d)] = float(close)
        finally:
            cur.close()
    except Exception:  # noqa: BLE001 — 프록시 없음 취급(빈 지도)
        return {}
    return out


def build_pairs(px, tv, meta, min_days=120, min_adv=1e8):
    """(codeA, codeB, kind) 목록. kind = pref|etf."""
    adv = {}
    for code, series in tv.items():
        vals = list(series.values())
        if vals:
            adv[code] = float(np.mean(vals[-120:]))
    by_name = {}
    for code, (name, kind) in meta.items():
        by_name.setdefault(name.strip(), code)

    pairs = []
    for code, (name, kind) in meta.items():
        if kind != "STOCK":
            continue
        m = PREF_RE.match(name.strip())
        if not m:
            continue
        base = m.group(1).strip()
        other = by_name.get(base)
        if not other or other == code:
            continue
        if len(px.get(code, {})) >= min_days and len(px.get(other, {})) >= min_days \
                and adv.get(code, 0) >= min_adv and adv.get(other, 0) >= min_adv:
            pairs.append((other, code, "pref"))          # (보통주, 우선주)

    etf_by_core = defaultdict(list)
    for code, (name, kind) in meta.items():
        if kind != "ETF":
            continue
        core = name.strip()
        for b in BRANDS:
            if core.startswith(b):
                core = core[len(b):].strip()
                break
        etf_by_core[core].append(code)
    for core, codes in etf_by_core.items():
        codes = [c for c in codes if len(px.get(c, {})) >= min_days and adv.get(c, 0) >= min_adv]
        for i in range(len(codes)):
            for j in range(i + 1, len(codes)):
                pairs.append((codes[i], codes[j], "etf"))
    return pairs, adv


def breadth_index(px):
    """전 종목 일별 수익률 중앙값 → 누적 지수(시장 프록시)."""
    by_date = defaultdict(list)
    for code, series in px.items():
        ds = sorted(series)
        for prev, cur in zip(ds, ds[1:]):
            r = series[cur] / series[prev] - 1.0
            if -0.5 < r < 0.5:
                by_date[cur].append(r)
    dates = sorted(by_date)
    idx, level = {}, 1.0
    for d in dates:
        level *= 1.0 + float(np.median(by_date[d]))
        idx[d] = level
    return idx


def regime_map(idx, lookback=20):
    dates = sorted(idx)
    out = {}
    for i, d in enumerate(dates):
        if i < lookback:
            continue
        out[d] = "down" if idx[d] < idx[dates[i - lookback]] else "up"
    return out


def pair_series(a, b):
    common = sorted(set(a) & set(b))
    return common, np.array([a[d] for d in common]), np.array([b[d] for d in common])


def simulate(pair, px, regime, *, mode, z_entry, z_exit, z_stop, lookback, max_hold,
             fee_pct, slip_bps):
    codeA, codeB, kind = pair
    dates, pa, pb = pair_series(px.get(codeA, {}), px.get(codeB, {}))
    n = len(dates)
    if n < lookback + 30:
        return []
    la, lb = np.log(pa), np.log(pb)
    trades = []
    pos = None
    for t in range(lookback, n - 1):
        win_a, win_b = la[t - lookback + 1:t + 1], lb[t - lookback + 1:t + 1]
        var_b = float(np.var(win_b))
        beta = float(np.cov(win_a, win_b)[0, 1] / var_b) if var_b > 0 else 1.0
        beta = min(max(beta, 0.2), 5.0)
        spread = la[:t + 1] - beta * lb[:t + 1]
        w = spread[t - lookback + 1:t + 1]
        sd = float(np.std(w))
        if sd <= 0:
            continue
        z = (spread[t] - float(np.mean(w))) / sd

        if pos is not None:
            held = t - pos["t"]
            hit_exit = abs(z) <= z_exit or (z * pos["sign"]) <= -z_stop or held >= max_hold
            if hit_exit:
                # 일별 누적 손익(두 다리)으로 청산 수익률 계산: 로그가격 변화 기반
                gross = pos["sign"] * ((la[t] - la[pos["t"]]) - pos["beta"] * (lb[t] - lb[pos["t"]]))
                if mode == "long-only":
                    gross = (la[t] - la[pos["t"]]) if pos["sign"] > 0 else (lb[t] - lb[pos["t"]])
                legs = 2 if mode == "both" else 1
                cost = (fee_pct + 2 * slip_bps / 100.0) / 100.0 * legs
                net = gross - cost
                trades.append({
                    "kind": kind, "a": codeA, "b": codeB,
                    "entry": dates[pos["t"]], "exit": dates[t],
                    "hold": held, "z_entry": pos["z"],
                    "net_pct": 100.0 * net, "regime": regime.get(dates[pos["t"]], None),
                })
                pos = None
            continue

        if abs(z) >= z_entry:
            pos = {"t": t, "sign": 1.0 if z < 0 else -1.0, "beta": beta, "z": float(z)}
            if mode == "long-only":
                # 싼 다리만 매수: z>0 이면 B 가 싸다
                pos["sign"] = 1.0 if z > 0 else -1.0
    return trades


def summarize(trades):
    if not trades:
        return {"n": 0, "win_pct": None, "expectancy_pct": None, "total_pct": 0.0,
                "max_dd_pct": None, "avg_hold": None}
    vals = np.array([t["net_pct"] for t in trades], dtype=float)
    cum = np.cumsum(vals)
    peak = np.maximum.accumulate(cum)
    dd = float(np.max(peak - cum)) if len(cum) else 0.0
    return {
        "n": int(len(vals)),
        "win_pct": float(100 * np.mean(vals > 0)),
        "expectancy_pct": float(np.mean(vals)),
        "median_pct": float(np.median(vals)),
        "total_pct": float(np.sum(vals)),
        "max_dd_pct": dd,
        "avg_hold": float(np.mean([t["hold"] for t in trades])),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="쌍둥이 매매 하락장 실측")
    ap.add_argument("--mode", choices=("both", "long-only"), default="both")
    ap.add_argument("--z-entry", type=float, default=2.0)
    ap.add_argument("--z-exit", type=float, default=0.5)
    ap.add_argument("--z-stop", type=float, default=3.5)
    ap.add_argument("--lookback", type=int, default=60)
    ap.add_argument("--max-hold", type=int, default=20)
    ap.add_argument("--min-adv", type=float, default=100_000_000)
    ap.add_argument("--min-days", type=int, default=120)
    ap.add_argument("--fee-round-trip-pct", type=float, default=0.21)
    ap.add_argument("--slippage-bps", type=float, default=10)
    ap.add_argument("--use-index", default=None,
                    help="시장 프록시를 index_daily 실지수로 대체(예: KOSPI) —"
                         " spec_index_backfill §2. 기본은 기존 breadth 유지")
    ap.add_argument("--json-out", default=None)
    ap.add_argument("--md-out", default=None)
    args = ap.parse_args(argv)

    db = connect()
    px, tv, meta, n_market_rows = load(db)
    if args.use_index:
        idx = load_index_series(db, args.use_index)
        proxy_note = ("시장 프록시 = index_daily %s 실지수 종가(--use-index)"
                      % args.use_index)
    else:
        idx = breadth_index(px)
        proxy_note = ("시장 프록시 = 전 종목 일별 수익률 중앙값 누적(breadth)"
                      " (기본 — --use-index 로 index_daily 실지수 대체 가능)")
    n_index_rows = len(idx) if args.use_index else 0
    db.close()
    regime = regime_map(idx)
    pairs, adv = build_pairs(px, tv, meta, args.min_days, args.min_adv)

    trades = []
    for p in pairs:
        trades.extend(simulate(p, px, regime, mode=args.mode, z_entry=args.z_entry,
                               z_exit=args.z_exit, z_stop=args.z_stop, lookback=args.lookback,
                               max_hold=args.max_hold, fee_pct=args.fee_round_trip_pct,
                               slip_bps=args.slippage_bps))

    splits = {
        "all": summarize(trades),
        "down": summarize([t for t in trades if t["regime"] == "down"]),
        "up": summarize([t for t in trades if t["regime"] == "up"]),
    }
    by_kind = {}
    for kind in ("pref", "etf"):
        kt = [t for t in trades if t["kind"] == kind]
        by_kind[kind] = {"all": summarize(kt),
                         "down": summarize([t for t in kt if t["regime"] == "down"])}

    down = splits["down"]
    verdict = {
        "down_market_positive": bool(down["expectancy_pct"] is not None and down["expectancy_pct"] > 0),
        "down_n": down["n"], "down_expectancy_pct": down["expectancy_pct"],
        "note": ("하락 레짐(down)에서 기대값 > 0" if (down["expectancy_pct"] or 0) > 0
                 else "하락 레짐에서 기대값 <= 0 (표본 없으면 null)"),
    }

    out = {
        "mode": args.mode, "params": vars(args), "n_pairs": len(pairs),
        "pairs": [{"a": a, "b": b, "kind": k} for a, b, k in pairs],
        "regime_counts": {"down": sum(1 for v in regime.values() if v == "down"),
                          "up": sum(1 for v in regime.values() if v == "up")},
        "splits": splits, "by_kind": by_kind, "verdict": verdict,
        "data_note": (proxy_note
                      + ". ETF NAV 괴리 이력은 2026-11-19부터 축적 중이라 미포함."),
        # 자기신고 3분리(소스 수신/파서 생성/실제 저장) — DB 읽기 전용 분석이라
        # dq_claim.record_claim 은 미사용(money_scoreboard 관례: 출력에 내장).
        # 재실행 시 같은 파라미터면 같은 파일을 덮어쓴다(멱등 — 실행 시각 미기록).
        "claim": {
            "runner": "pairs_arb_research",
            "source_rows": {"market_data": n_market_rows,
                            "index_daily": n_index_rows},
            "parsed_rows": {"pairs": len(pairs), "trades": len(trades)},
            "persisted_rows": {"json": 1, "md": 1},
        },
    }

    prefix = os.path.join(REPO, "data", "reports", "pairs_arb_%s" % args.mode.replace("-", "_"))
    json_out = args.json_out or prefix + ".json"
    md_out = args.md_out or prefix + ".md"
    os.makedirs(os.path.dirname(json_out), exist_ok=True)
    with open(json_out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
        fh.write("\n")

    lines = ["# 쌍둥이 매매 실측 — mode=%s" % args.mode, "",
             "쌍 %d개 · 거래 %d건 · 레짐 down %d일/up %d일" % (
                 len(pairs), len(trades), out["regime_counts"]["down"], out["regime_counts"]["up"]),
             "", "| 구분 | n | 승률 | 기대값(%%) | 중앙(%%) | 누적(%%) | MaxDD(%%p) | 평균보유 |",
             "|---|---|---|---|---|---|---|---|"]
    for label, s in splits.items():
        lines.append("| %s | %d | %s | %s | %s | %.2f | %s | %s |" % (
            label, s["n"],
            "-" if s["win_pct"] is None else "%.1f" % s["win_pct"],
            "-" if s["expectancy_pct"] is None else "%.4f" % s["expectancy_pct"],
            "-" if s.get("median_pct") is None else "%.4f" % s["median_pct"],
            s["total_pct"],
            "-" if s["max_dd_pct"] is None else "%.2f" % s["max_dd_pct"],
            "-" if s["avg_hold"] is None else "%.1f" % s["avg_hold"]))
    lines += ["", "**판정**: " + verdict["note"], "", "데이터: " + out["data_note"]]
    with open(md_out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")

    print("pairs=%d trades=%d | all: n=%d exp=%s | down: n=%d exp=%s | up: n=%d exp=%s" % (
        len(pairs), len(trades), splits["all"]["n"], splits["all"]["expectancy_pct"],
        down["n"], down["expectancy_pct"], splits["up"]["n"], splits["up"]["expectancy_pct"]))
    print("판정:", verdict["note"])
    print("[claim] pairs_arb_research source=%d(market_data)+%d(index_daily) claimed=%d pairs/%d trades persisted=2 (json+md)"
          % (n_market_rows, n_index_rows, len(pairs), len(trades)))
    print("저장:", json_out, md_out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
