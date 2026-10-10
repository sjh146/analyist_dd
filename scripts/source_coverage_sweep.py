#!/usr/bin/env python3
"""미배선 원천 전수 스윕 — DB 의 **모든** 테이블을 청정 패널 유니버스 대비로 채점해
'패널 구간을 덮는 신규 정보원'이 남아 있는지 기계적으로 판정한다.

WHY (2026-10-10 엔지니어 자율, 헌장 §3 자율 범위 = 실험 등록·판정·축 종결):
    data_axis_readiness.py 는 이미 등록된 4축(인트라데이·공매도·뉴스·SNS)만 채점한다.
    그런데 "그 4축 말고 더 없나?" 는 지금까지 **사람이 기억으로** 답해 왔다
    (2026-10-04 전수 스크린 노트). 수집기가 새 원천을 붙이면 그 사실을 아무도 자동으로
    알아채지 못하고, 엔지니어는 'pending 없음' 상태에서 밤을 넘긴다.

    이 스크립트는 그 질문을 **재실행 가능한 게이트**로 만든다:
      게이트 = ① 종목 코드 컬럼 있음 ② 날짜 컬럼 있음(시점가변) ③ 패널 커버 ≥ 0.75
               ④ 거래일 ≥ 250(420일 패널의 폴드를 덮을 이력)
    통과하는데 이미 배선된 원천도 아니면 NEW_CANDIDATE 로 출력한다 → 그때 피처 실험을
    등록하면 된다. 통과하지 않으면 **막고 있는 정확한 숫자**(cover/days)를 남긴다.

읽기 전용. 운영 DB 스키마를 건드리지 않는다. host 에 psycopg2 가 없어 docker exec psql 을 쓰고,
질의는 테이블 수와 무관하게 **psql 2회**로 배치한다(틱에서 수 초 안에 끝나야 한다).

사용:
    python3 scripts/source_coverage_sweep.py            # 표 + 게이트
    python3 scripts/source_coverage_sweep.py --json out.json
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PANEL = "/app/app/models/wf/panel_prod200.npz"      # 컨테이너 경로(청정 패널)
EVID = os.path.join(PROJ, "data", "reports", "source_coverage_sweep.json")

# 게이트 문턱
MIN_COVER = 0.75        # 패널 200종목 중 150종목
MIN_DAYS = 250          # 420일 패널의 확장창 폴드를 덮을 이력

# 이미 피처로 배선된 원천(패널 213피처의 입력). 여기 있으면 신규 후보가 아니다.
# disclosures 를 포함하는 이유: 파생 테이블 event_features(191/200 커버·315일)로 이미 배선됐고,
# 단변량 스크린에서 공시·지분·계약 이벤트 18종 AUC 0.497~0.500 = 무정보로 종결됐다(L3/CG60).
WIRED = {
    "market_data", "stock_prices", "supply_market_features", "financial_ratio_features",
    "event_features", "financial_statements", "ml_predictions", "disclosures",
}
# 실측으로 '부분 커버'가 확정된 원천 — 스윕이 매번 새 후보로 오보하지 않도록 사유를 고정한다.
# (사유는 백로그 항목 note 에 원문이 있다. 여기엔 숫자만.)
CLOSED_NOTE = {
    "minute_bars": "XR26 미수리(30봉 꼬리) — CG129/CG101",
    "krx_short_selling": "수집 범위 11/200 — CG141",
    "news_events": "커버리지 15/200 · 이력 192일 — CG73/CG60",
    "sns_posts": "커버리지 22/200 · 이력 190일 — CG73/CG60",
    "sns_post_features": "위와 동일 원천의 피처 테이블",
    "foreign_institutional": "커버리지 87/200 — 값 보유 338종목 · 단변량 IC +0.0045~+0.0082(t<0.7) = 무정보 실측",
    "ownership": "이력 9일 — 소스 미축적",
    "stock_sentiment": "이력 3일 — 신설",
    "news_event_extraction": "이력 4일 — 신설",
    "news_analysis": "이력 4일(700행) — news_events 파생",
}
CODE_PREF = ["stock_code", "code", "short_code", "ticker", "std_id", "isin"]
DATE_PREF = ["trade_date", "date", "event_date", "asof_date", "rcept_dt", "posted_at",
             "published_at", "created_at", "datetime"]


def psql(sql: str, timeout: int = 90) -> list[list[str]]:
    cmd = ["docker", "exec", "stock_postgres", "psql", "-U", "stock_user", "-d", "stock_trading",
           "-t", "-A", "-F", "|", "-c", sql]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if out.returncode != 0:
        raise RuntimeError(f"psql rc={out.returncode}: {out.stderr.strip()[:200]}")
    return [l.rstrip().split("|") for l in out.stdout.splitlines() if l.strip()]


def panel_codes() -> tuple[set[str], dict]:
    """청정 패널의 종목 코드 + 스냅샷(파일 mtime·행수·기간) — 증거 형식 요구사항."""
    code = ("import numpy as np,os,json;z=np.load(%r,allow_pickle=True);"
            "d=sorted(set(str(x) for x in z['dates']));"
            "print(','.join(sorted(set(str(x) for x in z['codes']))));"
            "print('#'+json.dumps({'shape':list(z['X'].shape),"
            "'dates':len(d),'start':d[0],'end':d[-1],"
            "'mtime':__import__('time').strftime('%%Y-%%m-%%dT%%H:%%M:%%S',"
            "__import__('time').localtime(os.path.getmtime(%r)))}))" % (PANEL, PANEL))
    out = subprocess.run(["docker", "exec", "stock_xgboost_ml", "python", "-c", code],
                         capture_output=True, text=True, timeout=180)
    if out.returncode != 0:
        raise RuntimeError(f"패널 읽기 실패: {out.stderr.strip()[:200]}")
    lines = [l for l in out.stdout.strip().splitlines() if l]
    codes = {c for c in lines[0].split(",") if c}
    snap = json.loads(lines[1][1:]) if len(lines) > 1 and lines[1].startswith("#") else {}
    return codes, snap


def introspect() -> dict[str, list[str]]:
    rows = psql("SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema='public' ORDER BY table_name, ordinal_position;")
    tabs: dict[str, list[str]] = {}
    for t, c in rows:
        tabs.setdefault(t, []).append(c)
    return tabs


def chunked_batch(tabs: dict[str, list[str]], pc: set[str]) -> dict[str, dict]:
    """테이블 전부를 psql 1~2회로 채점한다(틱 소요 시간 상수화)."""
    rec: dict[str, dict] = {}
    with_both, code_only, plain = [], [], []
    for t, cs in tabs.items():
        ccol = next((c for c in CODE_PREF if c in cs), None)
        dcol = next((c for c in DATE_PREF if c in cs), None)
        rec[t] = {"table": t, "code_col": ccol, "date_col": dcol}
        (with_both if (ccol and dcol) else code_only if ccol else plain).append(t)

    def run(batch, sql_for):
        if not batch:
            return
        sql = " UNION ALL ".join(sql_for(t) for t in batch) + ";"
        for r in psql(sql, timeout=300):
            rec[r[0]].update(json.loads(r[1]))

    def both_sql(t):
        c, d = rec[t]["code_col"], rec[t]["date_col"]
        return (f"SELECT '{t}', json_build_object('rows',count(*),'n_codes',count(DISTINCT {c}),"
                f"'n_days',count(DISTINCT {d}::date),'span',min({d}::date)::text||'~'||max({d}::date)::text)"
                f"::text FROM public.{t}")

    def plain_sql(t):
        return f"SELECT '{t}', json_build_object('rows',count(*))::text FROM public.{t}"

    run(with_both, both_sql)
    run(code_only, plain_sql)
    run(plain, plain_sql)

    # 커버리지는 코드 집합이 있어야 하므로 코드 보유 테이블만 개별 1회씩(수십 종목 테이블뿐).
    for t in with_both + code_only:
        r = rec[t]
        if not r.get("rows"):
            continue
        c = r["code_col"]
        codes = {x[0] for x in psql(f"SELECT DISTINCT {c} FROM public.{t};", timeout=120)}
        r["panel_cover_n"] = len(codes & pc)
        r["panel_cover_frac"] = round(len(codes & pc) / max(1, len(pc)), 3)
    return rec


def classify(r: dict) -> str:
    if r.get("rows") == 0:
        return "EMPTY"
    if not r.get("code_col"):
        return "MARKET_LEVEL"          # 종목 축이 없다 → 횡단면 모델에 그대로 못 넣는다
    if r["table"] in WIRED:
        return "WIRED"
    if r["table"] in CLOSED_NOTE:
        return "CLOSED"
    if not r.get("date_col") or (r.get("date_col") == "created_at" and (r.get("n_days") or 0) <= 5):
        return "STATIC"                # 종목당 1행(종목상수)·메타 타임스탬프 → 시점가변 아님
    cover, days = r.get("panel_cover_frac") or 0.0, r.get("n_days") or 0
    if cover >= MIN_COVER and days >= MIN_DAYS:
        return "NEW_CANDIDATE"
    return "BLOCKED"


def render_md(res: dict) -> str:
    """사람이 읽는 증거(MD) — 표 + 게이트."""
    snap = res.get("panel_snapshot") or {}
    L = [f"# 미배선 원천 전수 스윕 — {res.get('ts')}", "",
         f"- 스냅샷: `{res.get('panel')}` {snap.get('shape')} · {snap.get('dates')}일 "
         f"({snap.get('start')}~{snap.get('end')}) · mtime {snap.get('mtime')}",
         f"- 게이트: 종목코드 + 날짜 컬럼 + 커버리지 ≥ {res.get('min_cover')} "
         f"+ 거래일 ≥ {res.get('min_days')} (패널 유니버스 {res.get('panel_universe')})",
         f"- **결과: {summary_line(res)}**", "",
         "| 판정 | 테이블 | 행 | 종목 | 거래일 | 패널커버 | 사유 |", "|---|---|---|---|---|---|---|"]
    for r in res.get("tables", []):
        if r["verdict"] == "EMPTY":
            continue
        L.append("| {} | `{}` | {} | {} | {} | {} | {} |".format(
            r["verdict"], r["table"], r.get("rows", ""), r.get("n_codes", ""),
            r.get("n_days", ""), r.get("panel_cover_frac", ""),
            CLOSED_NOTE.get(r["table"], "") if r["verdict"] == "CLOSED" else ""))
    L += ["", "판정 뜻: WIRED=이미 피처 배선 · CLOSED=실측 무정보/부분커버 확정 · "
              "MARKET_LEVEL=종목축 없음(횡단면 입력 불가) · STATIC=종목상수/메타 · "
              "BLOCKED=게이트 미달 · NEW_CANDIDATE=피처 배선 실험 등록 대상", ""]
    return "\n".join(L)


def sweep(json_out: str = EVID) -> dict:
    """전 테이블 채점 → 증거 JSON 기록 → 결과 dict. (구동기에서 import 해 쓰는 진입점)"""
    pc, snap = panel_codes()
    tabs = introspect()
    rec = chunked_batch(tabs, pc)
    for r in rec.values():
        r["verdict"] = classify(r)
    order = {"NEW_CANDIDATE": 0, "BLOCKED": 1, "CLOSED": 2, "WIRED": 3,
             "STATIC": 4, "MARKET_LEVEL": 5, "EMPTY": 6}
    rows = sorted(rec.values(), key=lambda r: (order[r["verdict"]],
                                               -(r.get("panel_cover_frac") or 0)))
    res = {"ts": __import__("datetime").datetime.now().astimezone().isoformat(timespec="seconds"),
           "panel": "panel_prod200.npz", "panel_snapshot": snap,
           "panel_universe": len(pc), "min_cover": MIN_COVER, "min_days": MIN_DAYS,
           "new_candidates": [r["table"] for r in rows if r["verdict"] == "NEW_CANDIDATE"],
           "tables": rows}
    with open(json_out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    try:
        with open(os.path.splitext(json_out)[0] + ".md", "w", encoding="utf-8") as f:
            f.write(render_md(res))
    except OSError:
        pass                                        # 증거 MD 실패가 스윕을 죽이지 않게
    return res


def summary_line(res: dict) -> str:
    """틱 한 줄 요약 — 게이트 결과만."""
    cand = res.get("new_candidates") or []
    if cand:
        return ("신규 후보 " + ", ".join(cand) + " → 피처 배선 실험 등록 필요")
    rows = res.get("tables", [])
    n_blocked = sum(1 for r in rows if r["verdict"] == "BLOCKED")
    n_closed = sum(1 for r in rows if r["verdict"] == "CLOSED")
    n_market = sum(1 for r in rows if r["verdict"] == "MARKET_LEVEL")
    return (f"신규 후보 없음 (BLOCKED {n_blocked} · CLOSED {n_closed} · 시장레벨 {n_market} "
            "→ 남은 레버는 수집 데이터뿐)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", dest="json_out", default=EVID)
    a = ap.parse_args()

    res = sweep(a.json_out)
    rows = res["tables"]
    print(f"미배선 원천 스윕 (패널 유니버스 {res['panel_universe']}종목 · "
          f"게이트 cover≥{MIN_COVER} · days≥{MIN_DAYS})")
    snap = res.get("panel_snapshot") or {}
    if snap:
        print(f"  스냅샷: {res['panel']} {snap.get('shape')} · {snap.get('dates')}일 "
              f"({snap.get('start')}~{snap.get('end')}) · mtime {snap.get('mtime')}")
    for r in rows:
        if r["verdict"] == "EMPTY":
            continue
        det = " · ".join(f"{k}={r.get(k)}" for k in ("rows", "n_codes", "n_days", "panel_cover_frac")
                         if r.get(k) is not None)
        note = CLOSED_NOTE.get(r["table"], "") if r["verdict"] == "CLOSED" else ""
        print(f"  [{r['verdict']:13}] {r['table']:24} {det}" + (f"  ↳ {note}" if note else ""))
    print("게이트: " + summary_line(res))
    print(f"증거: {a.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
