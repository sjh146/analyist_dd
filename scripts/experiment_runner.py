#!/usr/bin/env python3
"""experiment_runner.py — T1 자율 탐색: 사전등록 격자로 전략 파라미터를 **돈 지표**로 섀도 평가.

WHY (2026-10-04): 자율 루프가 수익 쪽으로 개선하려면 '탐색'이 자동이어야 한다. 그런데 AUC 축은
돈과 무관하다(실측: 단일분할↔다중폴드 −0.81, AUC↔순기대 +0.10/−0.24, n=8). 그래서 탐색도
**체결 가능 순기대(%p/세션)** 로 하고, 격자를 코드에 **사전등록**한다(사후 조정 = p-hacking 방지).

먼저 재학습이 필요 없는 축을 본다 — 선별 깊이(top-K)·체결성 필터(당일등락 상한)·청산 규칙.
싸고, 되돌리기 쉽고, 무엇보다 즉시 '돈'으로 측정된다. (라벨·피처 축은 재학습이 필요하므로
엔지니어 틱/파이프라인 몫이고, 그쪽도 objective 의 돈 지표로 판정된다.)

원칙
- 수용 기준은 `config/objective.json` 의 acceptance 를 그대로 쓴다(문서=코드 단일 진실원).
- 비교 기준선은 **같은 CSV 안의 현행 조합**(topk=3·cap=25·next_open) — 외부 측정과 섞지 않는다.
- 이미 평가한 조합은 registry 로 건너뛴다(같은 데이터 재평가 금지).
- 통과 조합은 **제안(protected)** 으로 큐에 넣는다: 선별·청산을 바꾸는 일은 실주문 경로라 자동 반영 금지.

사용:
  python3 scripts/experiment_runner.py --run          # 격자 평가(호스트, DB 조회 1회)
  python3 scripts/experiment_runner.py --list          # registry 요약·통과 조합
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import itertools
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))
import fillable_expectancy as fe  # noqa: E402
import improve_queue as iq  # noqa: E402
from objective import load as load_objective  # noqa: E402

TRADES = os.path.join(REPO, "data", "reports", "close_gate_probe", "trades.csv")
REGISTRY = os.path.join(REPO, "data", "state", "experiments.json")
OUT_DIR = os.path.join(REPO, "data", "reports", "experiments")
LEDGER = os.path.join(REPO, "data", "reports", "experiments.jsonl")

# ── 사전등록 격자 (여기 있는 조합만 평가한다 — 사후 추가는 커밋으로 기록) ──────────────
# ⚠ 체결성 필터(당일등락 상한)는 **선택 축이 아니라 필수 조건**이다. 실측 2026-10-02: 필터 없이
#   재면 상한가(살 수 없는) 종목이 +5.23%p/세션을 만들어 '양(+) 기대'로 보이지만, 필터를 걸면
#   −0.67%p 로 뒤집힌다. 그래서 cap=None 조합은 격자에서 제외하고, 판정에서도 다시 막는다.
GRID = {
    "topk": [1, 2, 3, 5],
    "max_day_chg": [10.0, 15.0, 20.0, 25.0],
    "exit": ["next_open", "next_close"],
    # 선택 방향 — 지금까지는 '점수 상위'만 봤다. 시장 패턴은 반대편에 있을 수 있다:
    #  · score_bottom : 모델이 가장 낮게 본 종목(비선호 = 역추세)
    #  · daychg_low   : 당일 상승폭이 가장 작은 종목(과열 회피 = 되돌림)
    "select": ["score_top", "score_bottom", "daychg_low"],
    # 실제 매매 경로는 R1/HEAT 게이트를 통과한 종목만 산다. 측정도 그 조건으로 봐야 정직하다.
    "gated": [True, False],
    # 체결 가능 대역(당일등락 %) — 지금 스크리너는 상한가(+25~30%)를 뽑아 82%가 살 수 없는 종목이다.
    # "상승 중이지만 상한가가 아닌" 구간에서 고르면 체결 가능하고 추세도 이어질 수 있다는 가설을 측정한다.
    "band": ["none", "p0_10", "p3_12", "p5_15"],
}
INCUMBENT = {"topk": 3, "max_day_chg": 25.0, "exit": "next_open",
             "select": "score_top", "gated": True, "band": "none"}   # 현행 트레이더 설정
MAX_FILLABLE_CAP = 25.0
BANDS = {"none": None, "p0_10": (0.0, 10.0), "p3_12": (3.0, 12.0), "p5_15": (5.0, 15.0)}


def protocol_hash() -> str:
    blob = json.dumps(GRID, sort_keys=True) + json.dumps(INCUMBENT, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


def combo_key(c: dict) -> str:
    return "k{topk}_cap{max_day_chg}_{exit}_{select}_g{0}_b{band}".format(
        1 if c["gated"] else 0,
        **{k: c[k] for k in ("topk", "max_day_chg", "exit", "select", "band")})


def data_fingerprint(path: str) -> str:
    """평가 대상 데이터가 바뀌면 같은 조합도 **다시** 평가해야 한다(안 그러면 루프가 무동작).

    실측 사고(2026-10-04): registry 를 조합 키로만 건너뛰니 첫 실행 뒤로는 매일 no-op 이 되어
    '자율 탐색'이 죽은 루프가 됐다. 파일 지문(크기·행수·최근 세션)을 함께 저장해 새 데이터에서 재평가한다.
    """
    try:
        st = os.stat(path)
        return "{0}_{1}_{2}".format(st.st_size, int(st.st_mtime), _csv_sessions(path))
    except OSError:
        return "no-file"


def _csv_sessions(path: str) -> int:
    try:
        import pandas as pd
        return int(pd.read_csv(path, usecols=["date"])["date"].nunique())
    except Exception:
        return -1


def _args_for(combo: dict, args):
    ns = argparse.Namespace()
    ns.max_day_chg = combo["max_day_chg"]
    ns.exclude_limit_up = False
    ns.limit_up_pct = fe.LIMIT_UP_PCT
    ns.min_price = None
    ns.max_price = None
    ns.min_value = None
    ns.topk = combo["topk"]
    ns.exit = combo["exit"]
    ns.fee_buy, ns.fee_sell, ns.tax_sell = args.fee_buy, args.fee_sell, args.tax_sell
    return ns


def _select_variants(t):
    """선택 규칙별 뷰를 만든다 — simulate 는 score 내림차순만 하므로 부호/축을 바꿔 표현한다."""
    out = {}
    for sel in GRID["select"]:
        tv = t.copy()
        if sel == "score_bottom":
            tv["score"] = -tv["score"]
        elif sel == "daychg_low":
            tv["score"] = -tv["day_change_pct"]
        out[sel] = tv
    return out


def _apply_gate(tv):
    """실제 매매 경로와 같은 조건: R1·HEAT·게이트를 통과한 종목만."""
    mask = None
    for col in ("gate_ok", "r1_ok", "heat_ok"):
        if col not in tv.columns:
            return tv.iloc[0:0]                      # 컬럼이 없으면 '통과분'을 주장할 수 없다
        c = tv[col].fillna(False).astype(bool)
        mask = c if mask is None else (mask & c)
    return tv[mask]


def evaluate(t, combos: list[dict], args) -> dict:
    """격자 평가 — DB 조회/수익률 계산은 한 번만(t 를 재사용), 조합별로 필터·시뮬만 돈다."""
    variants = _select_variants(t)
    out: dict[str, dict] = {}
    for c in combos:
        ns = _args_for(c, args)
        base = variants[c["select"]]
        if c["gated"]:
            base = _apply_gate(base)
        band = BANDS.get(c["band"])
        if band is not None:                          # 체결 가능 대역 필터(살 수 있는 구간만)
            dc = base["day_change_pct"]
            base = base[(dc >= band[0]) & (dc <= band[1])]
        t_f, dropped = fe.apply_filters(base, ns)
        rt = fe.round_trip(ns)
        per_session, dates, _detail = fe.simulate(t_f, ns.topk, fe.EXIT_COLS[ns.exit], rt)
        st = fe.session_stats(per_session, dates)
        sh = fe.split_half(per_session, dates) or {}
        out[combo_key(c)] = {"combo": c, "n_rows": int(len(t_f)), "dropped": int(dropped),
                             "round_trip_pct": rt,
                             "avg_pct": st.get("avg_pct"), "t_stat": st.get("t_stat"),
                             "n_sessions": st.get("n_sessions"), "n_trades": int(len(t_f)),
                             "halves": {"front": (sh.get("front") or {}).get("avg_pct"),
                                        "back": (sh.get("back") or {}).get("avg_pct"),
                                        "stable": sh.get("stable")},
                             "worst_session_pct": st.get("worst_session_pct")}
    return out


def verdicts(results: dict, obj: dict) -> dict:
    """수용 판정 — objective.json 그대로 + 같은 CSV 안의 현행 조합 대비 개선."""
    acc = obj["goal"]["acceptance"]
    margin = float(acc["min_improvement_pct_over_incumbent"])   # %p 단위
    inc_key = combo_key(INCUMBENT)
    inc = (results.get(inc_key) or {}).get("avg_pct")
    out = {}
    for k, r in results.items():
        why = []
        exp = r.get("avg_pct")
        if exp is None:
            out[k] = {"pass": False, "why": ["측정 불가"]}
            continue
        # 체결성 가드 — 필터 없는 조합은 '살 수 없는 종목'을 포함하므로 통과시킬 수 없다(방어 2중).
        cap = (r.get("combo") or {}).get("max_day_chg")
        if cap is None or float(cap) > MAX_FILLABLE_CAP:
            out[k] = {"pass": False, "avg_pct": exp,
                      "why": [f"체결성 필터 없음/과대(cap={cap}) — 상한가 착시 구간(실측 +5.23→−0.67%p)"]}
            continue
        if exp <= float(acc.get("min_expectancy_pct", 0.0)):
            why.append(f"순기대 {exp:+.3f}%p ≤ {acc.get('min_expectancy_pct', 0.0)}")
        if (r.get("n_sessions") or 0) < int(acc["min_sample_sessions"]):
            why.append(f"세션 {r.get('n_sessions')} < {acc['min_sample_sessions']}")
        if (r.get("n_trades") or 0) < int(acc["min_sample_trades"]):
            why.append(f"표본 {r.get('n_trades')} < {acc['min_sample_trades']}")
        if (r.get("halves") or {}).get("stable") != "both_positive":
            why.append(f"분할표본 불안정({(r.get('halves') or {}).get('front')}/"
                       f"{(r.get('halves') or {}).get('back')})")
        if inc is not None and k != inc_key and exp < float(inc) + margin:
            why.append(f"현행 조합 {float(inc):+.3f}%p 대비 개선 부족(≥ +{margin})")
        out[k] = {"pass": not why, "why": why, "avg_pct": exp,
                  "delta_vs_incumbent": (None if inc is None or k == inc_key else round(exp - float(inc), 4))}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="T1 사전등록 격자 탐색(돈 지표)")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--trades", default=TRADES)
    ap.add_argument("--fee-buy", type=float, default=fe.FEE_BUY)
    ap.add_argument("--fee-sell", type=float, default=fe.FEE_SELL)
    ap.add_argument("--tax-sell", type=float, default=fe.TAX_SELL)
    ap.add_argument("--force", action="store_true", help="registry 무시하고 재평가")
    a = ap.parse_args(argv)

    try:
        reg = json.load(open(REGISTRY, encoding="utf-8"))
    except (OSError, ValueError):
        reg = {"protocol": protocol_hash(), "tested": {}, "runs": []}
    if reg.get("protocol") != protocol_hash():
        reg = {"protocol": protocol_hash(), "tested": {}, "runs": []}   # 격자 변경 → 새 기준

    if a.list:
        print(json.dumps({"protocol": reg["protocol"], "tested": len(reg.get("tested") or {}),
                          "passing": [k for k, v in (reg.get("tested") or {}).items() if v.get("pass")],
                          "runs": reg.get("runs", [])[-3:]}, ensure_ascii=False, indent=2))
        return 0
    if not a.run:
        ap.print_help()
        return 0
    if not os.path.exists(a.trades):
        print(f"[experiment] 후보 CSV 없음: {a.trades}", file=sys.stderr)
        return 2

    combos = [dict(zip(GRID, v)) for v in itertools.product(*GRID.values())]
    fp = data_fingerprint(a.trades)
    tested = reg.get("tested") or {}
    todo = combos if a.force else [c for c in combos
                                   if (tested.get(combo_key(c)) or {}).get("fp") != fp]
    if not todo:
        print(json.dumps({"tested_total": len(tested), "data_fp": fp,
                          "note": "새 조합 없음(같은 데이터로 격자 전부 평가됨)",
                          "passing": [k for k, v in tested.items() if v.get("pass")]},
                         ensure_ascii=False))
        return 0

    obj = load_objective()
    t = fe.load_trades(a.trades)
    t = fe.enrich_from_db(t, fe.db_connect())
    results = evaluate(t, todo, a)
    verd = verdicts(results, obj)

    reg.setdefault("tested", {}).update({k: {"pass": v["pass"], "avg_pct": v.get("avg_pct"),
                                             "why": v["why"], "combo": results[k]["combo"],
                                             "fp": fp,
                                             "at": dt.datetime.now().isoformat(timespec="seconds")}
                                         for k, v in verd.items()})
    os.makedirs(REGISTRY.rsplit("/", 1)[0], exist_ok=True)
    with open(REGISTRY, "w", encoding="utf-8") as f:
        json.dump(reg, f, ensure_ascii=False, indent=2)

    os.makedirs(OUT_DIR, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    report = {"when": stamp, "protocol": reg["protocol"], "incumbent": INCUMBENT,
              "acceptance": obj["goal"]["acceptance"], "results": results, "verdicts": verd}
    with open(os.path.join(OUT_DIR, f"grid_{stamp}.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    passing = [k for k, v in verd.items() if v["pass"]]
    with open(LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps({"when": stamp, "tested": len(results), "passing": passing,
                            "best": max(results, key=lambda k: results[k]["avg_pct"] or -9),
                            "best_avg_pct": max((results[k]["avg_pct"] or -9) for k in results)},
                           ensure_ascii=False) + "\n")

    # 통과 조합은 '제안'으로 큐에 넣는다(선별·청산 변경 = 실주문 경로 → 자동 반영 금지)
    q = iq._load()
    for k in passing:
        c = results[k]["combo"]
        iq.add(q, f"실험 통과 조합 검토: {k}",
               f"사전등록 격자에서 통과(순기대 {results[k]['avg_pct']:+.3f}%p, "
               f"세션 {results[k]['n_sessions']}, 현행 대비 {verd[k]['delta_vs_incumbent']:+}%p) — "
               f"선별·청산 변경은 실주문 경로라 사람 검토 필요",
               ["트레이더 설정 변경안·되돌리기 절차 문서화", "동일 프로토콜 재측정으로 재현 확인"],
               priority=1, protected=True, source="experiment")
    if passing:
        iq._save(q)

    rank = sorted(results, key=lambda k: (results[k]["avg_pct"] or -9), reverse=True)
    print(json.dumps({"evaluated": len(results), "passing": passing,
                      "incumbent_key": combo_key(INCUMBENT),
                      "incumbent_avg_pct": (results.get(combo_key(INCUMBENT)) or {}).get("avg_pct"),
                      "top8": [{"key": k, "avg_pct": results[k]["avg_pct"], "t": results[k]["t_stat"],
                                "n": results[k]["n_sessions"], "halves": results[k]["halves"]["stable"],
                                "pass": verd[k]["pass"]} for k in rank[:8]]},
                     ensure_ascii=False, indent=2))
    return 0 if passing else 1


if __name__ == "__main__":
    sys.exit(main())
