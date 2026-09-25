#!/usr/bin/env python3
"""
퀀트 3역할 **목표 사슬 스코어보드** (north-star scoreboard).

WHY: 2026-09-25 사용자 지시 — "리서처=양질 데이터가 잘 정제되어 모델에 학습되게,
엔지니어=AUC 향상, 트레이더=돈을 더 벌기. 이 최종 목표를 위해 궁극적으로 일하게 하라."
그런데 그때까지 각 역할의 보고는 **"사이클을 돌았다"** 뿐이었고, 목표 지표가 없었다.
여기서 세 역할의 북극성을 **산출물에서 직접** 읽어 한 화면에 묶는다.

  최종 목표: 순손익(₩)
     └─ 트레이더  : 실현 순손익(₩) · 건당 기대값(₩) · 승률 · 보유/회전
     └─ 엔지니어  : 로버스트 AUC(확장창 워크포워드 다중폴드 평균) ↑
     └─ 리서처    : 모델에 들어가는 데이터의 품질·커버리지 (DQ 상태, 살아있는 피처)

원칙(문서 docs/QUANT_AGENT_OBJECTIVES.md 참조):
  1) 판정은 **산출물**에서 직접 읽는다(자기신고 금지). 출처를 화면에 밝힌다.
  2) 돈은 **%가 아니라 원**으로 본다 — 실측: 평균수익률 +0.32% 인데 총액 -7,839원(포지션 크기 차이).
  3) 무개선이 N사이클 지속되면 **사람 개입 필요**로 올린다(조용한 공전 금지).

사용:
  python3 scripts/quant_scoreboard.py                 # 사슬 전체 출력
  python3 scripts/quant_scoreboard.py --stanza trader # 한 역할만(구동기가 틱마다 사용)
  python3 scripts/quant_scoreboard.py --json          # 기계 판독용
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KST = timezone(timedelta(hours=9))

JOURNAL = "/mnt/c/Users/jhshi/analyist_dd/trader-agent/journal/trade_journal.sqlite3"
ME_LEDGER = os.path.join(PROJ, "data/reports/model_engineer_ledger.jsonl")
RES_LEDGER = os.path.join(PROJ, "data/reports/researcher_ledger.jsonl")
DQ_GLOB = os.path.join(PROJ, "data/reports/dq_snapshots/dq_*.json")
OUT_JSON = os.path.join(PROJ, "data/reports/quant_scoreboard.json")
OUT_MD = os.path.join(PROJ, "docs/QUANT_SCOREBOARD.md")

# 챔피언(현행) 단일분할 AUC 와 **최고 로버스트 기준선**.
# 기준선은 라벨·유니버스 실험에서 실측된 최선값(LS_quant_q30_h5, h5·분위0.3·top30).
# 신호 판정은 폴드 표준편차(±0.03)를 감안해 **+0.02 이상**만 인정한다.
BASELINE_ROBUST = 0.5406
BASELINE_NAME = "LS_quant_q30_h5 (h5·분위0.3·top30)"
SIGNAL_DELTA = 0.02
NO_IMPROVE_CYCLES = 3      # 사이클 3회 연속 무개선 → 사람 개입 요청
NO_TRADE_DAYS = 3          # 3거래일 신규 진입 없음 → 회전 정지 경고


# ── 공통 ────────────────────────────────────────────────────────────────────
def _docker_cat(path: str) -> str | None:
    for cname in ("stock_xgboost_ml",):
        try:
            r = subprocess.run(["docker", "exec", cname, "cat", path],
                               capture_output=True, text=True, timeout=25)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    return None


def _jsonl(path: str) -> list[dict]:
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except OSError:
        pass
    return out


# ── 트레이더: 돈 ────────────────────────────────────────────────────────────
def trader_stanza() -> dict:
    """실현 순손익을 **원**으로. 청산된 거래만 돈이 확정된다."""
    st = {"role": "trader", "north": "실현 순손익(₩)", "source": JOURNAL,
          "realized_krw": None, "trades": 0, "wins": 0, "win_rate": None,
          "expectancy_krw": None, "avg_return_pct": None, "open_positions": 0,
          "days_since_entry": None, "alerts": [], "error": None}
    if not os.path.exists(JOURNAL):
        st["error"] = "저널 없음"
        return st
    try:
        conn = sqlite3.connect(f"file:{JOURNAL}?mode=ro", uri=True, timeout=10)
        conn.row_factory = sqlite3.Row
        closed = [dict(r) for r in conn.execute(
            "SELECT * FROM trades WHERE exit_price IS NOT NULL AND exit_price > 0")]
        opens = [dict(r) for r in conn.execute(
            "SELECT * FROM trades WHERE exit_price IS NULL OR exit_price = 0")]
        last_ts = conn.execute("SELECT MAX(ts) FROM trades").fetchone()[0]
        conn.close()
    except sqlite3.Error as exc:
        st["error"] = f"저널 읽기 실패: {exc}"
        return st

    total, wins, rets = 0.0, 0, []
    for t in closed:
        entry, exit_ = float(t["price"] or 0), float(t["exit_price"] or 0)
        qty = abs(float(t["qty"] or 0))
        sign = 1.0 if (t.get("side") or "buy") == "buy" else -1.0
        pnl = (exit_ - entry) * qty * sign
        total += pnl
        if pnl > 0:
            wins += 1
        if entry:
            rets.append((exit_ - entry) / entry * 100 * sign)
    st.update({
        "realized_krw": round(total),
        "trades": len(closed),
        "wins": wins,
        "win_rate": round(wins / len(closed) * 100, 1) if closed else None,
        "expectancy_krw": round(total / len(closed)) if closed else None,
        "avg_return_pct": round(sum(rets) / len(rets), 2) if rets else None,
        "open_positions": len(opens),
    })
    if last_ts:
        try:
            t = datetime.fromisoformat(str(last_ts).replace("Z", "+00:00"))
            if t.tzinfo is None:
                t = t.replace(tzinfo=KST)
            st["days_since_entry"] = round((datetime.now(KST) - t).total_seconds() / 86400, 1)
        except ValueError:
            pass
    # ⚠ 돈 기준 판정: 수익률 평균이 +여도 총액이 -면 진다(포지션 크기 비대칭).
    if st["realized_krw"] is not None and st["realized_krw"] < 0:
        st["alerts"].append(f"순손익 마이너스 {st['realized_krw']:,}원 — 기대값 "
                            f"{st['expectancy_krw']:,}원/건")
    if st["days_since_entry"] is not None and st["days_since_entry"] >= NO_TRADE_DAYS:
        st["alerts"].append(f"신규 진입 {st['days_since_entry']}일 없음 "
                            f"(보유 {st['open_positions']}종목) — 돈이 도는 회전이 멈춤")
    return st


# ── 엔지니어: 로버스트 AUC ──────────────────────────────────────────────────
def engineer_stanza() -> dict:
    """로버스트(다중폴드 평균) AUC. 단일분할은 참고로만 병기한다."""
    st = {"role": "engineer", "north": "로버스트 AUC (다중폴드 평균)",
          "champion_single": None, "best_robust": None, "best_robust_std": None,
          "best_exp": None, "baseline": BASELINE_ROBUST, "baseline_name": BASELINE_NAME,
          "delta": None, "last_verdict": None, "no_improve_cycles": 0,
          "source": f"{ME_LEDGER} + /app/app/models/champion/auc.txt", "alerts": []}

    auc = _docker_cat("/app/app/models/champion/auc.txt")
    if auc:
        try:
            st["champion_single"] = round(float(auc.split()[0]), 6)
        except (ValueError, IndexError):
            pass

    best, best_std, best_exp = None, None, None
    for rec in _jsonl(ME_LEDGER):
        parsed = rec.get("parsed") or {}
        per = (parsed.get("per_exp") or {}) if isinstance(parsed, dict) else {}
        for exp, val in per.items():
            if not isinstance(val, dict):
                continue
            mean = val.get("mean")
            if isinstance(mean, (int, float)) and (best is None or mean > best):
                best, best_std, best_exp = float(mean), val.get("std"), exp
    if best is not None:
        st.update({"best_robust": round(best, 4),
                   "best_robust_std": round(float(best_std), 4) if isinstance(best_std, (int, float)) else None,
                   "best_exp": best_exp,
                   "delta": round(best - BASELINE_ROBUST, 4)})
    recs = _jsonl(ME_LEDGER)
    if recs:
        st["last_verdict"] = recs[-1].get("verdict")
    # 무개선 카운터는 **측정이 성립한 사이클**만 센다. rc!=0(소실·타임아웃)·요약 미갱신은
    # '측정'이 아니라서, 소실을 노이즈로 세면 '무개선 3사이클 → 새 레버 필요' 경보가 헛돈다
    # (실측 2026-09-25: U1 이 평일 20:00 컨테이너 재생성으로 소실됐는데 옛 L2 요약을 읽어
    #  Δ+0.0000 '노이즈'로 기록 → 무효 사이클이 카운터에 포함됐다).
    measured = [r for r in recs
                if r.get("rc") == 0
                and _rec_best_mean(r) is not None
                and not (isinstance(r.get("parsed"), dict) and r["parsed"].get("error"))]
    improved = sum(1 for r in measured
                   if (_rec_best_mean(r) or 0.0) - BASELINE_ROBUST >= SIGNAL_DELTA)
    st["no_improve_cycles"] = max(0, len(measured) - improved)
    st["measured_cycles"] = len(measured)
    st["invalid_cycles"] = len(recs) - len(measured)
    if st["no_improve_cycles"] >= NO_IMPROVE_CYCLES:
        st["alerts"].append(f"로버스트 AUC 가 {st['no_improve_cycles']}사이클(측정 {len(measured)}회) "
                            f"연속 기준선 ({BASELINE_ROBUST}) 대비 +{SIGNAL_DELTA} 미달 — 새 레버 필요 "
                            f"(사람 승인 대상)")
    return st


def _rec_best_mean(rec: dict) -> float | None:
    parsed = rec.get("parsed")
    per = (parsed.get("per_exp") or {}) if isinstance(parsed, dict) else {}
    vals: list[float] = []
    for v in per.values():
        if isinstance(v, dict) and isinstance(v.get("mean"), (int, float)):
            vals.append(float(v["mean"]))
    return max(vals) if vals else None


# ── 리서처: 데이터 품질 ─────────────────────────────────────────────────────
def researcher_stanza() -> dict:
    """모델에 들어가는 데이터의 품질·커버리지. '모았다'가 아니라 '쓸 수 있게 됐다'를 본다."""
    st = {"role": "researcher", "north": "모델 학습 데이터의 품질·커버리지",
          "sample": None, "status": None, "warns": [], "breaches": [],
          "alive_features": None, "dead_features": None,
          "stock_constant_ratio": None, "news_freshness_hours": None,
          "rows_delivered": None, "source": DQ_GLOB, "alerts": []}
    files = sorted(glob.glob(DQ_GLOB))
    if not files:
        st["alerts"].append("DQ 스냅샷 없음 — 리서처 사이클 미실행")
        return st
    st["sample"] = os.path.basename(files[-1])
    with open(files[-1], encoding="utf-8") as f:
        d = json.load(f)
    m = d.get("metrics") or {}
    st["warns"] = d.get("warns") or []
    st["breaches"] = d.get("breaches") or []
    st["status"] = "breach" if st["breaches"] else ("warn" if st["warns"] else "ok")

    def val(key):
        v = (m.get(key) or {}).get("value")
        return v

    st["alive_features"] = val("feature_alive_count")
    st["dead_features"] = val("feature_dead_count")
    st["stock_constant_ratio"] = val("dq_feature_stock_constant_ratio")
    st["news_freshness_hours"] = val("news_analysis_freshness_hours")
    st["rows_delivered"] = val("dq_claim_source")
    if st["breaches"]:
        st["alerts"].append("DQ 위반: " + " / ".join(st["breaches"]))
    if st["warns"]:
        st["alerts"].append("DQ 경고: " + " / ".join(st["warns"]))
    dead = st["dead_features"] or 0
    alive = st["alive_features"] or 0
    if alive and dead and dead > alive:
        st["alerts"].append(f"죽은 피처 {dead:.0f}개 > 살아있는 피처 {alive:.0f}개 — "
                            f"모델이 쓸 수 있는 신호가 소수")
    return st


# ── 사슬 요약 ───────────────────────────────────────────────────────────────
def build() -> dict:
    t, e, r = trader_stanza(), engineer_stanza(), researcher_stanza()
    people = []
    for s in (t, e, r):
        people.extend(f"[{s['role']}] {a}" for a in s.get("alerts") or [])
    return {"ts": datetime.now(KST).isoformat(timespec="seconds"),
            "goal": "최종 목표: 순손익(₩) — 리서처 데이터 → 엔지니어 AUC → 트레이더 돈",
            "trader": t, "engineer": e, "researcher": r, "needs_human": people}


def fmt(st: dict, with_source: bool = True) -> str:
    kst = st["ts"][11:16]
    t, e, r = st["trader"], st["engineer"], st["researcher"]
    L = []
    L.append(f"[퀀트 목표 사슬] {kst} — 최종 목표: 돈(순손익)")
    if t.get("error"):
        L.append(f"💰 트레이더   : 측정 불가 ({t['error']})")
    else:
        L.append(f"💰 트레이더   : 순손익 {t['realized_krw']:+,}원 | {t['trades']}건 "
                 f"승률 {t['win_rate']}% | 기대값 {t['expectancy_krw']:+,}원/건 | "
                 f"보유 {t['open_positions']}종목 | 마지막 진입 {t['days_since_entry']}일 전")
    if e.get("best_robust") is None:
        L.append("📈 모델엔지니어: 로버스트 측정값 없음")
    else:
        d = e["delta"]
        mark = "신호" if d >= SIGNAL_DELTA else ("유지" if d >= -0.005 else "악화")
        L.append(f"📈 모델엔지니어: 로버스트 {e['best_robust']:.4f}"
                 f"±{e['best_robust_std'] if e['best_robust_std'] is not None else '?'}"
                 f" ({e['best_exp']}) vs 기준선 {e['baseline']:.4f} → Δ{d:+.4f} [{mark}]"
                 f" | 챔피언 단일분할 {e['champion_single']}")
    L.append(f"🔬 퀀트리서처: DQ {r['status'] or 'n/a'} | 살아있는 피처 "
             f"{r['alive_features']:.0f} / 죽은 {r['dead_features']:.0f} | "
             f"종목상수 {r['stock_constant_ratio']:.3f} | 뉴스신선도 "
             f"{r['news_freshness_hours']:.2f}h")
    if st["needs_human"]:
        L.append("[사람 개입 필요]")
        L.extend("  - " + x for x in st["needs_human"])
    if with_source:
        L.append("연쇄: 리서처(데이터) → 엔지니어(AUC) → 트레이더(₩)")
    return "\n".join(L)


def write_md(st: dict) -> None:
    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
    body = ["# 퀀트 3역할 목표 스코어보드", "",
            f"- 갱신: {st['ts']}",
            f"- 목표: {st['goal']}", "",
            "```", fmt(st), "```", ""]
    if st["needs_human"]:
        body += ["## 🙋 사람 개입 필요", ""] + [f"- {x}" for x in st["needs_human"]] + [""]
    body += ["## 출처", "",
             f"- 트레이더: `{st['trader']['source']}` (실현 손익은 청산 거래에서만 계산)",
             f"- 엔지니어: `{st['engineer']['source']}`",
             f"- 리서처: `{st['researcher']['source']}` (최신 {st['researcher'].get('sample')})", ""]
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(body))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stanza", choices=["trader", "engineer", "researcher", "all"], default="all")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if a.stanza == "trader":
        s = trader_stanza()
        print(f"[북극성·트레이더] 순손익 {s['realized_krw']:+,}원 | 기대값 {s['expectancy_krw']:+,}원/건 | "
              f"승률 {s['win_rate']}% | 보유 {s['open_positions']}")
        for x in s["alerts"]:
            print(f"  ⚠ {x}")
        return 0
    if a.stanza == "engineer":
        s = engineer_stanza()
        print(f"[북극성·엔지니어] 로버스트 {s['best_robust']} vs 기준선 {s['baseline']} "
              f"(Δ{s['delta']}) | 무개선 {s['no_improve_cycles']}사이클"
              f" (측정 {s.get('measured_cycles')}·무효 {s.get('invalid_cycles')})")
        for x in s["alerts"]:
            print(f"  ⚠ {x}")
        return 0
    if a.stanza == "researcher":
        s = researcher_stanza()
        print(f"[북극성·리서처] DQ {s['status']} | 살아 {s['alive_features']:.0f}/죽은 {s['dead_features']:.0f} "
              f"| 뉴스 {s['news_freshness_hours']:.2f}h")
        for x in s["alerts"]:
            print(f"  ⚠ {x}")
        return 0

    st = build()
    if a.json:
        print(json.dumps(st, ensure_ascii=False, indent=2))
    else:
        print(fmt(st))
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    tmp = OUT_JSON + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)
    os.replace(tmp, OUT_JSON)
    write_md(st)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
