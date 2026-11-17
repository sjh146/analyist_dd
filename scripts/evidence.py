#!/usr/bin/env python3
"""근거 수집기(evidence) — 한 종목·한 시점의 판단 근거를 **여러 소스에서 모아** 점수화한다.

목적 (사용자 요구 2026-11-17):
  "거시경제·회사재무·언론 등 다양한 근거를 토대로 왜 보유 중인지, 어떤 조건이면 청산인지(테제 판정),
   청산된 종목이 왜 떨어졌는지 알 수 있어야 하고 피드백할 수 있어야 한다."
  → 테제 판정과 손실 사후분석이 같은 근거 팩을 쓰게 만든다(같은 사실을 두 곳에서 다르게 말하지 않도록).

소스(전부 as-of: 조회일 이후 데이터는 쓰지 않는다):
  price      market_data (1d/5d/20d 수익률, 유니버스·업종 상대, 거래량비)
  supply     foreign_institutional (외국인/기관 순매수 5일, 연속 매도일)
  news       news_events (최근 5일 이벤트 유형·긍/부정, 핵심 문장)
  disclosure disclosures (최근 14일 공시 유형)
  financial  financial_statements (최근 vs 전년동기: 매출·영업이익 성장, ROE·PER·PBR·부채)
  macro      macro_features (기준금리·환율·CPI·PPI·유가·장단기 스프레드 변화)
  us         us_market_data (직전 미국 세션: 나스닥/SOX/VIX)
반환: {"score": -1..1, "blocks": {...}, "evidence": [{"source","delta","note"}], "asof": "YYYY-MM-DD"}

사용:
  python3 scripts/evidence.py --code 290720 --asof 2026-11-17
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date

import psycopg2

NEG_EVENTS = ("소송", "상장폐지", "부도", "리콜", "규제", "CB", "유상증자", "횡령", "배임", "불성실")
POS_EVENTS = ("수주", "실적", "흑자", "M&A", "자사주", "배당", "신제품", "파트너십", "공급계약")


def pg():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", 5432)),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )


def _returns(cur, code: str, asof: str) -> dict:
    cur.execute("""SELECT trade_date, close_price, volume FROM market_data
                   WHERE stock_code=%s AND trade_date<=%s::date
                   ORDER BY trade_date DESC LIMIT 21""", (code, asof))
    rows = cur.fetchall()
    if len(rows) < 2:
        return {}
    px = [float(r[1]) for r in rows]
    vol = [float(r[2] or 0) for r in rows]
    out = {"last_date": str(rows[0][0]), "close": px[0]}
    for n, key in ((1, "ret_1d"), (4, "ret_5d"), (19, "ret_20d")):
        out[key] = round((px[0] / px[n] - 1) * 100, 2) if len(px) > n and px[n] else None
    out["vol_ratio"] = round(vol[0] / (sum(vol[1:21]) / max(len(vol[1:21]), 1)), 2) if len(vol) > 5 else None
    return out


def _universe_ret(cur, asof: str, sessions: int = 5) -> float | None:
    cur.execute("""WITH d AS (SELECT trade_date FROM market_data WHERE trade_date<=%s::date
                              GROUP BY 1 ORDER BY 1 DESC LIMIT %s)
                   SELECT (max(close_price) FILTER (WHERE trade_date=(SELECT min(trade_date) FROM d))
                           / NULLIF(max(close_price) FILTER (WHERE trade_date=(SELECT max(trade_date) FROM d)),0) - 1)*100
                   FROM market_data WHERE trade_date IN (SELECT trade_date FROM d) GROUP BY stock_code""",
                (asof, sessions + 1))
    vals = [float(r[0]) for r in cur.fetchall() if r[0] is not None]
    return round(sum(vals) / len(vals), 2) if vals else None


def _sector_ret(cur, code: str, asof: str, sessions: int = 5) -> tuple[str | None, float | None]:
    cur.execute("SELECT sector FROM stocks WHERE stock_code=%s", (code,))
    r = cur.fetchone()
    sector = r[0] if r else None
    if not sector:
        return None, None
    cur.execute("""WITH d AS (SELECT trade_date FROM market_data WHERE trade_date<=%s::date
                              GROUP BY 1 ORDER BY 1 DESC LIMIT %s),
                        peers AS (SELECT stock_code FROM stocks WHERE sector=%s)
                   SELECT (max(m.close_price) FILTER (WHERE m.trade_date=(SELECT min(trade_date) FROM d))
                           / NULLIF(max(m.close_price) FILTER (WHERE m.trade_date=(SELECT max(trade_date) FROM d)),0) - 1)*100
                   FROM market_data m JOIN peers p ON p.stock_code=m.stock_code
                   WHERE m.trade_date IN (SELECT trade_date FROM d) GROUP BY m.stock_code""",
                (asof, sessions + 1, sector))
    vals = [float(x[0]) for x in cur.fetchall() if x[0] is not None]
    return sector, (round(sum(vals) / len(vals), 2) if vals else None)


def _supply(cur, code: str, asof: str) -> dict:
    cur.execute("""SELECT trade_date, foreign_net_buy_qty, institution_net_buy_qty
                   FROM foreign_institutional WHERE stock_code=%s AND trade_date<=%s::date
                   ORDER BY trade_date DESC LIMIT 5""", (code, asof))
    rows = cur.fetchall()
    if not rows:
        return {}
    f = [int(r[1] or 0) for r in rows]
    i = [int(r[2] or 0) for r in rows]
    return {"days": len(rows), "foreign_sum": sum(f), "inst_sum": sum(i),
            "foreign_down_days": sum(1 for x in f if x < 0), "inst_down_days": sum(1 for x in i if x < 0)}


def _news(cur, code: str, asof: str, days: int = 5) -> dict:
    cur.execute("""SELECT event_type, count(*), max(representative_core_event_text)
                   FROM news_events WHERE stock_code=%s
                     AND first_article_at::date <= %s::date
                     AND first_article_at >= %s::date - interval '%s days'
                   GROUP BY 1 ORDER BY 2 DESC""", (code, asof, asof, days))
    rows = cur.fetchall()
    neg = sum(int(c) for t, c, _ in rows if any(k in (t or "") for k in NEG_EVENTS))
    pos = sum(int(c) for t, c, _ in rows if any(k in (t or "") for k in POS_EVENTS))
    head = [str(x[2])[:60] for x in rows[:2] if x[2]]
    return {"events": sum(int(c) for _t, c, _h in rows), "neg": neg, "pos": pos, "headlines": head,
            "types": [t for t, _c, _h in rows[:4]]}


def _disclosures(cur, code: str, asof: str, days: int = 14) -> dict:
    cur.execute("""SELECT report_nm, rcept_dt FROM disclosures
                   WHERE stock_code=%s AND rcept_dt<=%s::date AND rcept_dt >= %s::date - interval '%s days'
                   ORDER BY rcept_dt DESC LIMIT 6""", (code, asof, asof, days))
    rows = cur.fetchall()
    return {"count": len(rows), "names": [str(r[0])[:40] for r in rows[:3]]}


def _financial(cur, code: str, asof: str) -> dict:
    cur.execute("""SELECT report_date, revenue, operating_profit, net_income, roe, per, pbr, debt_ratio
                   FROM financial_statements WHERE stock_code=%s AND report_date<=%s::date
                   ORDER BY report_date DESC LIMIT 8""", (code, asof))
    rows = cur.fetchall()
    if not rows:
        return {}
    cur_r = rows[0]
    prev_year = next((r for r in rows if r[0] and cur_r[0] and
                      abs((r[0].replace(year=r[0].year + 1) - cur_r[0]).days) < 45), None)
    out = {"report_date": str(cur_r[0]), "revenue": float(cur_r[1] or 0), "op_profit": float(cur_r[2] or 0),
           "roe": float(cur_r[4]) if cur_r[4] is not None else None,
           "per": float(cur_r[5]) if cur_r[5] is not None else None,
           "pbr": float(cur_r[6]) if cur_r[6] is not None else None,
           "debt_ratio": float(cur_r[7]) if cur_r[7] is not None else None}
    if prev_year:
        pr = float(prev_year[1] or 0)
        po = float(prev_year[2] or 0)
        out["rev_yoy"] = round((out["revenue"] / pr - 1) * 100, 1) if pr else None
        out["op_yoy"] = round((out["op_profit"] / po - 1) * 100, 1) if po else None
    return out


def _macro(cur, asof: str) -> dict:
    cur.execute("""SELECT trade_date, fx_usd_krw, interest_rate, cpi_yoy, ppi_yoy, oil_wti, yield_spread
                   FROM macro_features WHERE trade_date<=%s::date ORDER BY trade_date DESC LIMIT 2""", (asof,))
    rows = cur.fetchall()
    if not rows:
        return {}
    r = rows[0]
    out = {"date": str(r[0]), "usdkrw": float(r[1]) if r[1] is not None else None,
           "rate": float(r[2]) if r[2] is not None else None,
           "cpi": float(r[3]) if r[3] is not None else None,
           "ppi": float(r[4]) if r[4] is not None else None,
           "oil": float(r[5]) if r[5] is not None else None,
           "spread": float(r[6]) if r[6] is not None else None}
    if len(rows) > 1:
        p = rows[1]
        for k, i in (("usdkrw", 1), ("rate", 2), ("oil", 5), ("spread", 6)):
            if r[i] is not None and p[i] not in (None, 0):
                out[f"{k}_chg"] = round((float(r[i]) / float(p[i]) - 1) * 100, 2) if k in ("usdkrw", "oil") \
                    else round(float(r[i]) - float(p[i]), 3)
    return out


def _us(cur, asof: str) -> dict:
    cur.execute("""SELECT index_name, trade_date, close_price FROM us_market_data
                   WHERE trade_date<=%s::date ORDER BY trade_date DESC, index_name""", (asof,))
    rows = cur.fetchall()
    if not rows:
        return {}
    latest = max(r[1] for r in rows)
    per = {}
    for name, d, c in rows:
        per.setdefault(name, []).append((d, float(c)))
    out = {"date": str(latest)}
    for name in ("NASDAQ", "SOX", "VIX"):
        seq = sorted(per.get(name, []), reverse=True)
        if len(seq) >= 2 and seq[1][1]:
            out[f"{name.lower()}_ret"] = round((seq[0][1] / seq[1][1] - 1) * 100, 2)
        if seq:
            out[name.lower()] = seq[0][1]
    return out


def collect(cur, code: str, asof: str) -> dict:
    asof = str(asof)[:10]
    blocks = {}
    ev: list[dict] = []

    px = _returns(cur, code, asof)
    uni = _universe_ret(cur, asof)
    sector, sec_ret = _sector_ret(cur, code, asof)
    blocks["price"] = {**px, "universe_ret_5d": uni, "sector": sector, "sector_ret_5d": sec_ret}
    if px.get("ret_5d") is not None and uni is not None:
        rel = round(px["ret_5d"] - uni, 2)
        blocks["price"]["rel_5d"] = rel
        ev.append({"source": "price", "delta": max(-0.30, min(0.30, rel / 20)),
                   "note": f"5일 수익률 {px['ret_5d']:+.2f}% (유니버스 {uni:+.2f}% → 상대 {rel:+.2f}%p)"})
    if sector and sec_ret is not None and px.get("ret_5d") is not None:
        rel_sec = round(px["ret_5d"] - sec_ret, 2)
        blocks["price"]["rel_sector_5d"] = rel_sec
        ev.append({"source": "sector", "delta": max(-0.10, min(0.10, rel_sec / 30)),
                   "note": f"업종({sector}) 5일 {sec_ret:+.2f}% 대비 {rel_sec:+.2f}%p"})
    if px.get("vol_ratio") and px["vol_ratio"] > 1.8:
        ev.append({"source": "price", "delta": -0.05 if (px.get("ret_1d") or 0) < 0 else 0.05,
                   "note": f"거래량 평균 대비 {px['vol_ratio']:.1f}배 (변동성 확대)"})

    sup = _supply(cur, code, asof)
    blocks["supply"] = sup
    if sup:
        fq = sup["foreign_sum"]
        ev.append({"source": "supply", "delta": max(-0.12, min(0.12, fq / 500000 * 0.12)),
                   "note": f"외국인 5일 순매수 {fq:+,}주 (매도일 {sup['foreign_down_days']}/5), 기관 {sup['inst_sum']:+,}주"})

    news = _news(cur, code, asof)
    blocks["news"] = news
    if news.get("events"):
        d = max(-0.20, min(0.20, 0.06 * news["neg"] - 0.04 * news["pos"]))
        ev.append({"source": "news", "delta": d,
                   "note": f"5일 이벤트 {news['events']}건 (긍정 {news['pos']}·부정 {news['neg']})" +
                           (f" · {news['headlines'][0]}" if news.get("headlines") else "")})
    else:
        ev.append({"source": "news", "delta": 0.0, "note": "최근 5일 뉴스 이벤트 0건 (정보 없음)"})

    disc = _disclosures(cur, code, asof)
    blocks["disclosure"] = disc
    if disc.get("count"):
        bad = any(k in (n or "") for n in disc["names"] for k in ("유상증자", "전환사채", "소송", "불성실", "감사의견"))
        ev.append({"source": "disclosure", "delta": -0.08 if bad else 0.0,
                   "note": f"14일 공시 {disc['count']}건" + (f" · {', '.join(disc['names'])}" if disc["names"] else "")})

    fin = _financial(cur, code, asof)
    blocks["financial"] = fin
    if fin:
        d = 0.0
        notes = []
        if fin.get("op_yoy") is not None:
            d += max(-0.10, min(0.10, fin["op_yoy"] / 500))
            notes.append(f"영업이익 YoY {fin['op_yoy']:+.1f}%")
        if fin.get("rev_yoy") is not None:
            notes.append(f"매출 YoY {fin['rev_yoy']:+.1f}%")
        if fin.get("roe") is not None:
            notes.append(f"ROE {fin['roe']:.1f}%")
        if fin.get("per") is not None:
            notes.append(f"PER {fin['per']:.1f}")
        ev.append({"source": "financial", "delta": round(d, 3),
                   "note": f"최근분기({fin['report_date']}) " + " · ".join(notes)})

    mac = _macro(cur, asof)
    blocks["macro"] = mac
    if mac:
        notes = []
        d = 0.0
        if mac.get("usdkrw_chg") is not None:
            notes.append(f"USDKRW {mac['usdkrw']:,.0f}({mac['usdkrw_chg']:+.2f}%)")
        if mac.get("rate") is not None:
            notes.append(f"기준금리 {mac['rate']:.2f}")
        if mac.get("cpi") is not None:
            notes.append(f"CPI {mac['cpi']:.1f}%")
        if mac.get("oil_chg") is not None:
            notes.append(f"WTI {mac['oil']:.1f}({mac['oil_chg']:+.2f}%)")
            d += max(-0.05, min(0.05, -mac["oil_chg"] / 100))
        ev.append({"source": "macro", "delta": round(d, 3), "note": " · ".join(notes)})

    us = _us(cur, asof)
    blocks["us"] = us
    if us:
        notes = []
        d = 0.0
        for k in ("nasdaq_ret", "sox_ret", "vix_ret"):
            if us.get(k) is not None:
                notes.append(f"{k.split('_')[0].upper()} {us[k]:+.2f}%")
                d += max(-0.06, min(0.06, us[k] / 100)) if k != "vix_ret" else max(-0.04, min(0.04, -us[k] / 200))
        ev.append({"source": "us", "delta": round(d, 3),
                   "note": f"직전 미국 세션({us['date']}): " + " · ".join(notes)})

    score = round(sum(e["delta"] for e in ev), 3)
    return {"code": code, "asof": asof, "score": max(-1.0, min(1.0, score)), "blocks": blocks, "evidence": ev}


def narrative(ev: dict) -> str:
    """사람이 읽는 한 문단 — 근거를 소스 순서대로 나열한다(숫자 인용, 결정적)."""
    lines = []
    for e in ev.get("evidence", []):
        tag = {"price": "가격", "sector": "업종", "supply": "수급", "news": "언론",
               "disclosure": "공시", "financial": "재무", "macro": "거시", "us": "미국장"}.get(e["source"], e["source"])
        lines.append(f"{tag}: {e['note']}")
    return " | ".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", required=True)
    ap.add_argument("--asof", default=date.today().isoformat())
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    conn = pg(); cur = conn.cursor()
    ev = collect(cur, a.code, a.asof)
    cur.close(); conn.close()
    if a.json:
        print(json.dumps(ev, ensure_ascii=False, indent=1, default=str))
    else:
        print(f"[{a.code} @ {a.asof}] 점수 {ev['score']:+.3f}")
        print(narrative(ev))
    return 0


if __name__ == "__main__":
    sys.exit(main())
