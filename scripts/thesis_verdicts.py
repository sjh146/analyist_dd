#!/usr/bin/env python3
"""보유 종목 테제(투자 가설) 시드 + 일일 판정.

왜 필요한가 (2026-11-17 실측):
  `position_theses` / `thesis_verdicts` 테이블과 `scripts/thesis_review.py`(조회·exit·rewrite),
  `scripts/thesis_onboarding.py`(초안 생성)는 이미 있는데 **행이 0건**이었다. 테제는 ackman(테제형 전략)
  진입에만 생성되고, 실제 보유(swing)는 아무 가설·반증조건 없이 손절/익절만으로 관리되고 있었다.
  → "왜 들고 있나 / 무엇이면 틀린 것인가"가 기록되지 않으니 손실이 나도 배울 대상이 없다.

이 스크립트가 하는 일:
  --seed    : 열린 보유(trader_fills.exit_ts IS NULL)에 테제가 없으면 생성(가설·반증조건·촉매).
  --judge   : active 테제마다 오늘의 판정(thesis_verdicts INSERT, append-only)을 계산.
              verdict ∈ {강화, 유지, 약화, 손상, 파기}, score ∈ [-1,1]
  --status  : 보유 + 최신 판정 + 점수/근거 출력(사람용).

판정 규칙(결정적, LLM 없음 — 재현 가능해야 함):
  score = 0.55*가격 + 0.20*상대강도 + 0.15*이벤트 + 0.10*시간
    · 가격     : (현재가-진입가)/진입가. -7%(손절선) 이하면 즉시 '파기'
    · 상대강도 : 종목 수익률 − 유니버스 평균 수익률(같은 기간, market_data)
    · 이벤트   : 최근 5일 부정 이벤트(소송·상장폐지·리콜·규제·부도·CB) 있으면 −, 긍정(수주·실적)이면 +
    · 시간     : 보유가 계획(스윙 5거래일)을 넘기면 −(가설이 검증되지 않은 채 자금이 묶임)

사용:
  POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5434 python3 scripts/thesis_verdicts.py --seed --judge --status
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request
from datetime import date, datetime, timedelta

import psycopg2
import psycopg2.extras

NEG_EVENTS = ("소송", "상장폐지", "부도", "리콜", "규제", "CB", "유상증자", "횡령", "배임")
POS_EVENTS = ("수주", "실적", "흑자", "M&A", "자사주", "배당", "신제품", "파트너십")
PLAN_HOLD_DAYS = 5      # 스윙 계획 보유(거래일)
STOP_PCT = -7.0


def pg():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", 5432)),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )


def now_price(code: str, cur) -> tuple[float | None, str]:
    """현재가: 네이버 모바일 API(장중 실시간) → 실패 시 market_data 최신 종가."""
    try:
        url = f"https://m.stock.naver.com/api/stock/{code}/basic"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            d = json.loads(r.read().decode())
        p = d.get("closePrice") or d.get("lastClosePrice")
        if p:
            return float(str(p).replace(",", "")), "naver"
    except Exception:
        pass
    cur.execute("SELECT close_price FROM market_data WHERE stock_code=%s ORDER BY trade_date DESC LIMIT 1", (code,))
    row = cur.fetchone()
    return (float(row[0]), "market_data") if row else (None, "없음")


def _bridge_positions() -> list[dict]:
    """브로커 보유 종목 — Windows 루프백 브리지는 Windows 프로세스만 접근 가능하므로 curl.exe 로 조회한다.
    (WSL 파이썬에서 127.0.0.1:8100 직접 호출은 000. --noproxy 필수 — 셸 http_proxy 를 물면 502.)"""
    curl = "/mnt/c/Windows/System32/curl.exe"
    try:
        out = subprocess.run([curl, "-s", "--noproxy", "*", "-m", "15",
                              "http://127.0.0.1:8100/positions"],
                             capture_output=True, text=True, timeout=25).stdout
        d = json.loads(out)
        return d.get("positions") or []
    except Exception as e:  # noqa: BLE001
        print(f"[thesis] 브리지 보유 조회 실패: {e}", file=sys.stderr)
        return []


def open_positions(cur):
    """열린 보유 = (code, name, qty, entry_price, entry_ts, screener, ml_prob).

    trader_fills 는 **청산된 거래만** 적재된다(실측 8행 중 open 0행) → 보유는 브리지(/positions)에서 받고,
    진입시각·스크리너·모델확률은 journal 파생 정보가 DB에 없으므로 trader_fills 이력에서 보강한다.
    """
    out = []
    for p in _bridge_positions():
        code = str(p.get("code") or "").strip()
        if not code:
            continue
        cur.execute("""SELECT screener, ml_prob, min(entry_ts) FROM trader_fills
                       WHERE code=%s GROUP BY screener, ml_prob ORDER BY min DESC LIMIT 1""", (code,))
        r = cur.fetchone() or (None, None, None)
        out.append((code, p.get("name"), int(p.get("qty") or 0), float(p.get("avg_price") or 0.0),
                    str(r[2]) if r[2] else None, r[0], r[1]))
    return out


def latest_thesis(cur, code):
    cur.execute("""SELECT id, thesis_text, disproof_criteria, status, entry_price
                   FROM position_theses WHERE stock_code=%s AND status='active'
                   ORDER BY id DESC LIMIT 1""", (code,))
    return cur.fetchone()


def seed(cur, apply=True):
    """열린 보유에 테제가 없으면 규칙 기반 테제 생성(LLM 없이 재현 가능)."""
    created = []
    for code, name, qty, entry, entry_ts, screener, ml_prob in open_positions(cur):
        if latest_thesis(cur, code):
            continue
        cur.execute("""SELECT event_type, count(*) FROM news_events
                       WHERE stock_code=%s AND first_article_at >= now() - interval '7 days'
                       GROUP BY 1 ORDER BY 2 DESC LIMIT 4""", (code,))
        cats = [{"type": t, "count": int(c)} for t, c in cur.fetchall()]
        prob = f"{float(ml_prob):.3f}" if ml_prob is not None else "미기록"
        thesis = (f"{name}({code}) {screener or 'swing'} 진입: 모델 확률 {prob}, 진입가 {float(entry):,.0f}원. "
                  f"가설 = 최근 신호(확률>소비자 문턱)와 이벤트 촉매가 단기(≈{PLAN_HOLD_DAYS}거래일) 상승 여력을 준다.")
        disproof = (f"① 종가가 진입가 대비 {STOP_PCT}% 이하(손절선 이탈) "
                    f"② {PLAN_HOLD_DAYS}거래일 내 수익률이 시장 평균 이하로 역전(상대강도 소멸) "
                    f"③ 부정 이벤트(소송·리콜·규제·CB 등) 5일 내 1건 이상")
        if apply:
            cur.execute("""INSERT INTO position_theses
                (stock_code, strategy_name, thesis_text, disproof_criteria, intrinsic_value,
                 entry_price, catalyst_events, status, decision_log)
                VALUES (%s,%s,%s,%s,NULL,%s,%s::jsonb,'active','[]'::jsonb) RETURNING id""",
                        (code, screener or "swing", thesis, disproof, entry, json.dumps(cats, ensure_ascii=False)))
            tid = cur.fetchone()[0]
        else:
            tid = None
        created.append({"code": code, "name": name, "thesis_id": tid, "catalysts": cats})
    return created


def ensure_evidence_table(cur) -> None:
    """근거 스냅샷 테이블 — thesis_verdicts 는 불변(트리거)이라 근거 문단은 여기에 따로 남긴다."""
    cur.execute("""CREATE TABLE IF NOT EXISTS thesis_evidence (
        id SERIAL PRIMARY KEY, thesis_id INTEGER NOT NULL REFERENCES position_theses(id),
        asof DATE NOT NULL, score NUMERIC, narrative TEXT, evidence JSONB,
        created_at TIMESTAMPTZ DEFAULT NOW(), UNIQUE (thesis_id, asof))""")


def judge(cur, apply=True, asof: date | None = None):
    asof = asof or date.today()
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import evidence as evmod          # 근거 수집기(가격·수급·언론·공시·재무·거시·미국장)
    out = []
    cur.execute("""SELECT DISTINCT ON (stock_code) id, stock_code, entry_price, disproof_criteria
                   FROM position_theses WHERE status='active'
                   ORDER BY stock_code, id DESC""")
    active = cur.fetchall()
    held = {r[0] for r in open_positions(cur)}
    for tid, code, entry, _dis in active:
        if code not in held:
            continue
        px, src = now_price(code, cur)
        if not px or not entry:
            continue
        entry = float(entry)
        ret = (px - entry) / entry * 100.0

        # ── 근거 팩(여러 소스) — 테제 판정과 손실 사후분석이 같은 근거를 쓰게 한다
        ev = evmod.collect(cur, code, str(asof))
        ev_score = float(ev.get("score") or 0.0)
        ev_text = evmod.narrative(ev)
        rel5 = (ev.get("blocks", {}).get("price", {}) or {}).get("rel_5d")

        # 보유 손익 성분(진입 대비)과 근거 성분을 반반 섞는다 — 손익만 보면 '이미 빠진 뒤'에야 반응한다
        pos_score = max(-1.0, min(1.0, ret / 7.0))
        score = round(0.5 * pos_score + 0.5 * ev_score, 3)

        cur.execute("SELECT min(entry_ts) FROM trader_fills WHERE code=%s", (code,))
        ets = cur.fetchone()[0]
        hold_days = 0
        if ets:
            try:
                hold_days = max(0, (datetime.now().astimezone() - datetime.fromisoformat(str(ets))).days)
            except Exception:
                hold_days = 0

        if ret <= STOP_PCT:
            verdict = "파기"
        elif score >= 0.30:
            verdict = "강화"
        elif score >= -0.05:
            verdict = "유지"
        elif score >= -0.30:
            verdict = "약화"
        elif score >= -0.60:
            verdict = "손상"
        else:
            verdict = "파기"

        summary = (f"진입대비 {ret:+.2f}%(현재 {px:,.0f}/{src}) · 근거점수 {ev_score:+.3f} · "
                   f"보유 {hold_days}일 || {ev_text}")[:1500]
        if apply:
            cur.execute("""INSERT INTO thesis_verdicts
                (thesis_id, verdict_date, verdict, verdict_score, evidence_event_ids,
                 evidence_summary, model_version)
                VALUES (%s,%s,%s,%s,%s,%s,'thesis_verdicts.py/v2-evidence')
                ON CONFLICT (thesis_id, verdict_date) DO NOTHING""",
                        (tid, asof, verdict, score, None, summary))
        if apply:
            ensure_evidence_table(cur)
            cur.execute("""INSERT INTO thesis_evidence (thesis_id, asof, score, narrative, evidence)
                VALUES (%s,%s,%s,%s,%s::jsonb)
                ON CONFLICT (thesis_id, asof) DO UPDATE SET
                  score=EXCLUDED.score, narrative=EXCLUDED.narrative, evidence=EXCLUDED.evidence""",
                        (tid, asof, ev_score, ev_text,
                         __import__('json').dumps(ev, ensure_ascii=False, default=str)))
        out.append({"thesis_id": tid, "code": code, "verdict": verdict, "score": score,
                    "ret_pct": round(ret, 2), "ev_score": ev_score, "rel5": rel5,
                    "summary": summary[:240]})
    return out


def status(cur):
    rows = []
    cur.execute("""SELECT DISTINCT ON (p.stock_code) p.id, p.stock_code, p.screener_dummy
                   FROM (SELECT id, stock_code, strategy_name AS screener_dummy FROM position_theses
                         WHERE status='active' ORDER BY id DESC) p""")
    for tid, code, _ in cur.fetchall():
        cur.execute("""SELECT verdict, verdict_score, verdict_date, evidence_summary
                       FROM thesis_verdicts WHERE thesis_id=%s ORDER BY verdict_date DESC, id DESC LIMIT 1""", (tid,))
        v = cur.fetchone()
        rows.append({"thesis_id": tid, "code": code,
                     "verdict": v[0] if v else "판정 없음",
                     "score": float(v[1]) if v else None,
                     "date": str(v[2]) if v else None,
                     "evidence": v[3] if v else ""})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", action="store_true")
    ap.add_argument("--judge", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if not (a.seed or a.judge or a.status):
        a.status = True

    conn = pg(); cur = conn.cursor(); rc = 0
    try:
        if a.seed:
            made = seed(cur, apply=not a.dry_run)
            print(f"[thesis] 시드 {len(made)}건 " + json.dumps(made, ensure_ascii=False))
        if a.judge:
            res = judge(cur, apply=not a.dry_run)
            for r in res:
                print(f"[판정] {r['code']} {r['verdict']:3s} score={r['score']:+.3f} | {r['summary']}")
        if a.status:
            for r in status(cur):
                print(f"[보유] {r['code']} 테제#{r['thesis_id']} {r['verdict']} "
                      f"score={r['score']} ({r['date']}) {r['evidence']}")
        if a.dry_run:
            conn.rollback()
            print("(dry-run — 롤백)")
        else:
            conn.commit()
    except Exception as e:
        conn.rollback(); rc = 1
        print(f"[thesis] ERROR {type(e).__name__}: {e}", file=sys.stderr)
    finally:
        cur.close(); conn.close()
    return rc


if __name__ == "__main__":
    sys.exit(main())
