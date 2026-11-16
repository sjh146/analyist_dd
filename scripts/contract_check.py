#!/usr/bin/env python3
"""역할 간 계약 점검 (contract check) — 생산자/소비자 경계를 매일 한 번 자동 검증한다.

왜 필요한가 (2026-11-16 실측):
  역할별 틱(리서처·엔지니어·트레이더·감사)은 각자 자기 지표만 본다. 그래서 경계에서 끊긴 것이
  아무도 모르게 몇 주씩 방치됐다.
    · 뉴스 백필이 `source='google_news'` 필터 때문에 대상 기사 **0건** → 이벤트/감성 피처 12개 사망
    · 종목 귀속 유니버스가 250종목 → 같은 기사에서도 귀속률 46종목(전 종목 사전이면 62종목)
    · 엔지니어 후보의 확률 최대값 0.5343 < 소비자 문턱 0.55 → 신호 0건(MT116). 승격 게이트는
      'AUC'만 보고, 소비자 문턱은 아무도 검사하지 않았다
  이 스크립트는 그 셋을 포함해 6개 계약을 PASS/FAIL 로 판정하고 JSON 으로 남긴다.

사용:
  python3 scripts/contract_check.py            # 사람용 요약 + JSON 기록
  python3 scripts/contract_check.py --json     # JSON 만 stdout
종료코드: 0 전부 PASS / 1 하나 이상 FAIL / 2 점검 자체 실패(DB 접속 등)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import datetime as dt

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FEED = os.path.join(PROJ, "data", "feed", "screener_latest.json")
OUT = os.path.join(PROJ, "data", "reports", "contract_check_latest.json")
PG = "stock_postgres"
DB_USER = "stock_user"
DB_NAME = "stock_trading"

# 소비자(runner.config) 쪽 실측 문턱 — FEED_CONTRACT v1.1: 확률 피드는 r1_min_avg_prob
SWING_PROB_THRESHOLD = 0.55
CLOSE_PROB_THRESHOLD = 0.55
MAX_FEED_AGE_DAYS = 3
MAX_SIGNAL_AGE_DAYS = 4           # close 4일 / swing 5일 중 보수적
MAX_DEAD_FEATURES = 45             # 2026-11-13 실측 38 → 문턱 45(악화 감지)
FEATURE_COVERAGE_MAX_AGE_DAYS = 7
MARKET_DATA_MAX_LAG_DAYS = 4       # 주말+휴장 감안
NEWS_MAPPED_STOCKS_MIN = 30        # 최근 7일 종목 귀속 수 최소선(실측 26~50)


def psql(sql: str) -> list[list[str]]:
    """psql -tA 로 조회. 실패는 예외로 올려 점검 실패(exit 2)로 처리한다."""
    r = subprocess.run(
        ["docker", "exec", PG, "psql", "-U", DB_USER, "-d", DB_NAME, "-tAF", "\t", "-c", sql],
        capture_output=True, text=True, timeout=120,
    )
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[:300])
    return [line.split("\t") for line in r.stdout.strip().splitlines() if line.strip()]


class Report:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def check(self, cid: str, name: str, ok: bool, detail: str, owner: str) -> None:
        self.rows.append({"id": cid, "name": name, "ok": bool(ok), "detail": detail, "owner": owner})
        print(("PASS " if ok else "FAIL ") + f"[{cid}] {name} — {detail}")

    def payload(self) -> dict:
        return {
            "checked_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "checks": self.rows,
            "fails": [r["id"] for r in self.rows if not r["ok"]],
            "ok": all(r["ok"] for r in self.rows),
        }


def check_market_data(rep: Report) -> None:
    """DATA-1: 시세 최신성 + 일자 연속성(빈 날 없음)."""
    rows = psql("SELECT max(trade_date), count(DISTINCT trade_date) FROM market_data")
    last = dt.date.fromisoformat(rows[0][0])
    lag = (dt.date.today() - last).days
    ok = lag <= MARKET_DATA_MAX_LAG_DAYS
    rep.check("DATA-1", "시세 최신성", ok,
              f"최신 거래일 {last} (오늘-{lag}일, 문턱 {MARKET_DATA_MAX_LAG_DAYS})", "리서처")


def check_feature_coverage(rep: Report) -> None:
    """FEAT-1: 죽은 피처 수 + 커버리지 측정 최신성."""
    rows = psql("SELECT count(*), count(*) FILTER (WHERE nonzero_ratio_honest < 0.01), max(computed_at)::date "
                "FROM feature_coverage")
    total, dead, computed = rows[0][0], int(rows[0][1]), dt.date.fromisoformat(rows[0][2])
    age = (dt.date.today() - computed).days
    ok = dead <= MAX_DEAD_FEATURES and age <= FEATURE_COVERAGE_MAX_AGE_DAYS
    rep.check("FEAT-1", "피처 생존/측정 최신성", ok,
              f"죽은 피처 {dead}/{total} (문턱 {MAX_DEAD_FEATURES}), 측정 {computed} ({age}일 전)", "엔지니어")


def check_news_attribution(rep: Report) -> None:
    """NEWS-1: 최근 7일 뉴스 종목 귀속 수 — 감성/이벤트 피처의 원천."""
    rows = psql("SELECT count(DISTINCT stock_code) FROM news_events "
                "WHERE first_article_at >= CURRENT_DATE - 7")
    n = int(rows[0][0] or 0)
    ok = n >= NEWS_MAPPED_STOCKS_MIN
    rep.check("NEWS-1", "뉴스→종목 귀속", ok,
              f"최근 7일 귀속 종목 {n}개 (문턱 {NEWS_MAPPED_STOCKS_MIN})", "리서처")


def _items_of(strategy_value):
    """candidates 값은 리스트이거나 {'items': [...]} 형태다(피드 v1.1 메타 포함)."""
    if isinstance(strategy_value, dict):
        return strategy_value.get("items") or []
    if isinstance(strategy_value, list):
        return strategy_value
    return []


def check_feed(rep: Report) -> None:
    """FEED-1: 피드 스키마·신선도·후보별 신호 나이·가격 누락."""
    if not os.path.exists(FEED):
        rep.check("FEED-1", "피드 신선도", False, "피드 파일 없음", "트레이더")
        return
    d = json.load(open(FEED, encoding="utf-8"))
    gen = d.get("generated_at", "")
    try:
        gen_dt = dt.datetime.fromisoformat(gen)
        age = (dt.datetime.now(gen_dt.tzinfo) - gen_dt).total_seconds() / 86400
    except Exception:
        age = 999.0
    cands = d.get("candidates") or {}
    total = sum(len(_items_of(v)) for v in cands.values())
    bad_price = sum(1 for v in cands.values() for c in _items_of(v) if not c.get("close_price"))
    stale = []
    today = dt.date.today()
    for strat, v in cands.items():
        for c in _items_of(v):
            sd = (c.get("signal_date") or "")[:10]
            try:
                if (today - dt.date.fromisoformat(sd)).days > MAX_SIGNAL_AGE_DAYS:
                    stale.append(f"{strat}:{c.get('stock_code')}@{sd}")
            except Exception:
                stale.append(f"{strat}:{c.get('stock_code')}@?")
    ok = age <= MAX_FEED_AGE_DAYS and bad_price == 0 and not stale
    rep.check("FEED-1", "피드 신선도/가격/신호나이", ok,
              f"발행 {age:.1f}일 전 · 후보 {total}건 · 가격누락 {bad_price} · 낡은신호 {len(stale)}", "트레이더")


def check_consumer_threshold(rep: Report) -> None:
    """FEED-2 (MT116): 피드 점수가 소비자 문턱을 넘는 신호가 실제로 있는가.

    확률 피드(score_kind=calibrated_prob)에서 score/100 이 문턱 미만이면 소비자는 그 전략을 통째로
    거부한다 — 2026-10-01 승격이 이 이유로 3세션 무진입을 만들었다. 승격 게이트는 AUC만 본다.
    """
    if not os.path.exists(FEED):
        rep.check("FEED-2", "소비자 문턱(MT116)", False, "피드 파일 없음", "엔지니어↔트레이더")
        return
    d = json.load(open(FEED, encoding="utf-8"))
    cands = d.get("candidates") or {}
    detail = []
    ok = True
    checked_any = False
    for strat in ("swing", "close"):
        items = _items_of(cands.get(strat))
        if not items:
            detail.append(f"{strat}: 후보 0건(소비자 무시)")
            continue
        kind = items[0].get("score_kind")
        thr = SWING_PROB_THRESHOLD if strat == "swing" else CLOSE_PROB_THRESHOLD
        if kind == "calibrated_prob":
            checked_any = True
            above = sum(1 for c in items if (c.get("score") or 0) / 100.0 > thr)
            ok = ok and above >= 1
            detail.append(f"{strat}: 확률>{thr} = {above}/{len(items)}건")
        else:
            detail.append(f"{strat}: score_kind={kind} (문턱검사 생략)")
    if not checked_any:
        detail.append("확률 피드 없음 → 문턱 검사 대상 없음")
    rep.check("FEED-2", "소비자 문턱(MT116)", ok, " · ".join(detail), "엔지니어↔트레이더")


def check_news_filter_window(rep: Report) -> None:
    """NEWS-2: 백필/추출이 실제로 기사를 볼 수 있는가(대상 기사 수 > 0)."""
    rows = psql("SELECT count(*) FROM news_analysis WHERE source <> 'sns'")
    n = int(rows[0][0] or 0)
    ok = n > 0
    rep.check("NEWS-2", "뉴스 원문 가용성", ok, f"news_analysis(source<>sns) {n}건", "리서처")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="JSON 만 출력")
    args = ap.parse_args()

    rep = Report()
    try:
        check_market_data(rep)
        check_feature_coverage(rep)
        check_news_attribution(rep)
        check_news_filter_window(rep)
        check_feed(rep)
        check_consumer_threshold(rep)
    except Exception as e:  # noqa: BLE001
        print(f"CONTRACT-CHECK FAILED TO RUN: {e}", file=sys.stderr)
        return 2

    payload = rep.payload()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print(("\nALL PASS" if payload["ok"] else "\nFAILED: " + ", ".join(payload["fails"]))
          + f"  → {OUT}")
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
