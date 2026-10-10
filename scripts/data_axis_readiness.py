#!/usr/bin/env python3
"""데이터 축 준비도 자동 판정 — needs_setup 데이터 항목의 '선행 조건'을 DB 실측으로 채점한다.

WHY (2026-10-10 엔지니어 자율, 헌장 §3 자율 범위 = 실험 등록):
    구동기 규칙 6 은 "pending 이 비면 상위 needs_setup 의 setup_needed 를 구현해 pending 으로
    승격하라"고 한다. 그런데 남은 세 데이터 항목(인트라데이 CG101/CG129 · 공매도 CG141 ·
    뉴스/SNS CG73/CG60)의 선행 조건은 **내가 만들 수 없는 수집 데이터**다 — 사람이 "이제
    됐나?" 를 매번 확인해야 한다. 그 확인을 기계화한다: DB 실측이 문턱을 넘으면 그 항목을
    pending 으로 자동 승격(자율)하고, 아니면 **막고 있는 정확한 숫자**를 보고한다.

    모델측 축은 19사이클 무개선으로 닫혔고(변환·HP·라벨·유니버스·앙상블·선별·가중·국면·
    창·정규화·보정), 남은 레버는 데이터 축 하나다(CG73). 즉 이 스크립트가 판정하는 문턱이
    곧 '다음 실험이 언제 가능한가'의 유일한 답이다.

읽기 전용. 운영 DB 스키마를 건드리지 않는다. host 에 psycopg2 가 없으므로
`docker exec stock_postgres psql` 로 질의하고, 패널 유니버스 코드는 컨테이너 python(numpy)로 읽는다.
`--promote` 는 준비된 축의 항목만 status=pending 으로 바꾼다(문턱 미달이면 아무것도 안 바꾼다).

사용:
    python3 scripts/data_axis_readiness.py                 # 표 출력
    python3 scripts/data_axis_readiness.py --json out.json # 증거 파일
    python3 scripts/data_axis_readiness.py --promote       # 준비된 항목만 pending 승격
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKLOG = os.path.join(PROJ, "docs", "QUANT_MODEL_BACKLOG.json")
PANEL = "/app/app/models/wf/panel_prod200.npz"  # 컨테이너 경로(청정 패널)

# 문턱 — 백로그 항목의 setup_needed/note 에 적힌 '해소 조건'에서 그대로 옮긴 값.
TH = {
    "intraday_bars_per_day": 300,   # 전 구간(09:00~15:30) ≈ 391봉. 30봉(꼬리)이면 XR26 미수리.
    "intraday_min_dates": 20,       # '6일 표본 한계' 해소 하한
    "panel_cover_frac": 0.75,       # 패널 유니버스(200) 대비 75% = 150종목
    "short_min_days": 250,          # 279일 패널 폴드 전체를 덮으려면 ≥250거래일 이력
    "news_min_days": 400,           # 420일 패널 창을 덮으려면 ≥400일 이력
}


def _psql(sql: str) -> list[list[str]]:
    """docker exec psql → 탭/파이프 구분 행. 실패는 예외로(조용히 0 을 반환하지 않는다)."""
    cmd = ["docker", "exec", "stock_postgres", "psql", "-U", "stock_user",
           "-d", "stock_trading", "-t", "-A", "-F", "|", "-c", sql]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise RuntimeError(f"psql 실패 rc={out.returncode}: {out.stderr.strip()[:300]}")
    rows = []
    for line in out.stdout.splitlines():
        line = line.rstrip()
        if not line:
            continue
        rows.append(line.split("|"))
    return rows


def panel_codes() -> set[str]:
    """청정 패널(prod200)의 종목 코드 집합 — 커버리지 분모."""
    code = (
        "import numpy as np;"
        f"z=np.load({PANEL!r},allow_pickle=True);"
        "c=sorted(set(str(x) for x in z['codes']));"
        "print(','.join(c))"
    )
    out = subprocess.run(["docker", "exec", "stock_xgboost_ml", "python", "-c", code],
                         capture_output=True, text=True, timeout=120)
    if out.returncode != 0:
        raise RuntimeError(f"패널 코드 읽기 실패: {out.stderr.strip()[:300]}")
    return {c for c in out.stdout.strip().split(",") if c}


def _f(x, default=0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def check_intraday() -> dict:
    """분봉 = '전 구간'이 적재됐는가(종목당 하루 봉수). 30봉 꼬리면 XR26 미수리."""
    rows = _psql(
        "WITH d AS (SELECT stock_code, trade_date, count(*) c FROM minute_bars GROUP BY 1,2) "
        "SELECT count(*), "
        "coalesce(percentile_cont(0.5) WITHIN GROUP (ORDER BY c),0), "
        "count(DISTINCT trade_date) FROM d;"
    )
    pairs = int(_f(rows[0][0])) if rows else 0
    med_bars = _f(rows[0][1]) if rows else 0.0
    n_dates = int(_f(rows[0][2])) if rows else 0
    codes = {r[0] for r in _psql("SELECT DISTINCT stock_code FROM minute_bars;")} if pairs else set()
    return {"pairs": pairs, "median_bars_per_day": med_bars, "n_dates": n_dates,
            "n_codes": len(codes), "codes": codes}


def check_short() -> dict:
    """공매도(krx_short_selling) 수집 범위·이력."""
    rows = _psql(
        "SELECT count(*), count(DISTINCT stock_code), count(DISTINCT trade_date), "
        "min(trade_date), max(trade_date) FROM krx_short_selling;"
    )
    r = rows[0] if rows else ["0", "0", "0", "", ""]
    codes = {x[0] for x in _psql("SELECT DISTINCT stock_code FROM krx_short_selling;")}
    return {"n_rows": int(_f(r[0])), "n_codes": int(_f(r[1])), "n_days": int(_f(r[2])),
            "min_date": r[3], "max_date": r[4], "codes": codes}


def check_news(table: str) -> dict:
    """뉴스/SNS 이력·커버리지. 테이블별 컬럼 차이를 흡수한다."""
    if table == "news_events":
        date_col, code_col = "event_date", "stock_code"
    elif table == "sns_posts":
        date_col, code_col = "posted_at", "stock_code"
    else:
        date_col, code_col = "trade_date", "stock_code"
    try:
        rows = _psql(
            f"SELECT count(*), count(DISTINCT {code_col}), "
            f"count(DISTINCT {date_col}::date), min({date_col}::date), max({date_col}::date) "
            f"FROM {table};"
        )
    except RuntimeError as e:
        return {"error": str(e)[:200], "codes": set()}
    r = rows[0] if rows else ["0", "0", "0", "", ""]
    codes = {x[0] for x in _psql(f"SELECT DISTINCT {code_col} FROM {table};")} if _f(r[0]) else set()
    return {"n_rows": int(_f(r[0])), "n_codes": int(_f(r[1])), "n_days": int(_f(r[2])),
            "min_date": r[3], "max_date": r[4], "codes": codes}


def evaluate(pc: set[str]) -> list[dict]:
    """축별 준비 판정. 각 축은 (준비여부, 항목 id, 막는 숫자)를 남긴다."""
    denom = max(1, len(pc))
    out = []

    # 1) 인트라데이 (CG129/CG101) — XR26 수리 + 전 구간 백필
    it = check_intraday()
    cover = len(it["codes"] & pc) / denom
    ready = (it["median_bars_per_day"] >= TH["intraday_bars_per_day"]
             and it["n_dates"] >= TH["intraday_min_dates"]
             and cover >= TH["panel_cover_frac"])
    out.append({
        "axis": "intraday", "items": ["CG129", "CG101"], "ready": ready,
        "blocking": "XR26(수집기 소유) 분봉 페이지네이션 미수리" if not ready else "",
        "measured": {"pairs": it["pairs"], "median_bars_per_day": it["median_bars_per_day"],
                     "n_dates": it["n_dates"], "panel_cover_frac": round(cover, 3)},
        "thresholds": {"median_bars_per_day": TH["intraday_bars_per_day"],
                       "n_dates": TH["intraday_min_dates"],
                       "panel_cover_frac": TH["panel_cover_frac"]},
    })

    # 2) 공매도 (CG141) — 수집 범위(패널 유니버스) + 이력
    sh = check_short()
    cover = len(sh["codes"] & pc) / denom
    ready = cover >= TH["panel_cover_frac"] and sh["n_days"] >= TH["short_min_days"]
    out.append({
        "axis": "short_selling", "items": ["CG141"], "ready": ready,
        "blocking": "수집 범위(수집기 소유)·이력 누적 필요" if not ready else "",
        "measured": {"n_rows": sh["n_rows"], "n_codes": sh["n_codes"], "n_days": sh["n_days"],
                     "span": f"{sh['min_date']}~{sh['max_date']}",
                     "panel_cover_frac": round(cover, 3)},
        "thresholds": {"panel_cover_frac": TH["panel_cover_frac"], "n_days": TH["short_min_days"]},
    })

    # 3) 뉴스/SNS (CG73/CG60) — 커버리지 + 이력
    best = None
    for tbl in ("news_events", "sns_posts"):
        d = check_news(tbl)
        if d.get("error"):
            out.append({"axis": f"news::{tbl}", "items": ["CG73"], "ready": False,
                        "blocking": f"질의 실패: {d['error']}", "measured": {}, "thresholds": {}})
            continue
        cover = len(d["codes"] & pc) / denom
        ready = cover >= TH["panel_cover_frac"] and d["n_days"] >= TH["news_min_days"]
        rec = {"axis": f"news::{tbl}", "items": ["CG73", "CG60"], "ready": ready,
               "blocking": "커버리지(패널 유니버스)·이력(≥400d) 동시 필요" if not ready else "",
               "measured": {"n_rows": d["n_rows"], "n_codes": d["n_codes"], "n_days": d["n_days"],
                            "span": f"{d['min_date']}~{d['max_date']}",
                            "panel_cover_frac": round(cover, 3)},
               "thresholds": {"panel_cover_frac": TH["panel_cover_frac"],
                              "n_days": TH["news_min_days"]}}
        out.append(rec)
        if best is None or (rec["ready"] and not best["ready"]):
            best = rec
    return out


def load_backlog() -> dict:
    with open(BACKLOG, encoding="utf-8") as f:
        return json.load(f)


def save_backlog(b: dict) -> None:
    tmp = BACKLOG + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(b, f, ensure_ascii=False, indent=2)
    os.replace(tmp, BACKLOG)


def needs_fields_ok(item: dict) -> list[str]:
    """규칙: command·counterfactual·success 가 없으면 pending 승격 불가(판정 불가 실험)."""
    return [k for k in ("command", "counterfactual", "success") if not item.get(k)]


def promote(axes: list[dict], backlog: dict) -> list[tuple[str, str]]:
    """준비된 축의 항목만 pending 으로 승격. 미달이면 아무것도 바꾸지 않는다."""
    ready_ids = {iid for a in axes if a["ready"] for iid in a["items"]}
    if not ready_ids:
        return []
    changed = []
    for it in backlog.get("items", []):
        iid = it.get("id")
        if iid not in ready_ids or it.get("status") != "needs_setup":
            continue
        missing = needs_fields_ok(it)
        if missing:
            changed.append((iid, f"보류(missing {','.join(missing)})"))
            continue
        it["status"] = "pending"
        changed.append((iid, "pending"))
    return changed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", dest="json_out", default=None)
    ap.add_argument("--promote", action="store_true")
    a = ap.parse_args()

    pc = panel_codes()
    axes = evaluate(pc)
    print(f"데이터 축 준비도 (패널 유니버스 {len(pc)}종목 · panel_prod200)")
    for ax in axes:
        flag = "READY" if ax["ready"] else "BLOCKED"
        m = ax["measured"]
        detail = " · ".join(f"{k}={v}" for k, v in m.items() if k != "codes")
        print(f"  [{flag:7}] {ax['axis']:18} {detail}")
        if not ax["ready"] and ax["blocking"]:
            print(f"            ↳ {ax['blocking']}")

    if a.json_out:
        cp = json.loads(json.dumps(axes))
        with open(a.json_out, "w", encoding="utf-8") as f:
            json.dump({"panel_universe": len(pc), "axes": cp}, f, ensure_ascii=False, indent=2)
        print(f"증거: {a.json_out}")

    if a.promote:
        b = load_backlog()
        changed = promote(axes, b)
        if changed:
            save_backlog(b)
            for iid, st in changed:
                print(f"승격: {iid} → {st}")
        else:
            print("승격 없음 — 준비된 축 없음(문턱 미달).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
