#!/usr/bin/env python3
"""모드 B 측정 감사 — 숫자가 '어떻게 만들어졌나' + 라이브 스코어가 살아있나 (읽기 전용).

역할: quant-auditor (docs/QUANT_ROLE_PLAN_V2.md §5.3 모드 B).
실측 근거: MT116 — 배포 챔피언이 같은 유니버스에서 0.55 초과 신호 0건(max 0.4733)이라
batch_type 이 signal→raw_fallback 으로 뒤집혀 3세션 무진입. 이 검사가 그것을 릴리스 당일 잡는다.

출력: reports/audit/measure_<YYYY-MM-DD>.json · 이상만 stdout.
종료코드: 0 정상 / 2 이상.
"""
import datetime as dt
import glob
import json
import os
import sys

REPO = "/home/jhshi/analyist_dd"
REL = os.path.join(REPO, "services", "xgboost-ml", "app", "models")
OUT_DIR = os.path.join(REPO, "reports", "audit")
CONF_TS = 0.55
SUB_AUC_SPREAD_WARN = 0.02   # 서브모델 AUC 격차가 이보다 크면 배포(균등평균)≠검증(가중평균) 위험


def load(p, default=None):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def champion_info():
    d = os.path.join(REL, "champion")
    info = {"dir": d, "auc": None, "robust": {}, "n_features": None, "backups": []}
    try:
        info["auc"] = float(open(os.path.join(d, "auc.txt")).read().strip())
    except Exception:  # noqa: BLE001
        pass
    info["robust"] = load(os.path.join(d, "robust_auc.json"), {})
    fn = load(os.path.join(d, "feature_names.json"), [])
    info["n_features"] = len(fn) if isinstance(fn, list) else None
    info["backups"] = sorted(os.path.basename(x) for x in glob.glob(os.path.join(REL, "champion_prev_*")))
    return info


def live_scores():
    """피드/스크리너 산출물의 스코어 분포 (전략별)."""
    out = {}
    feed = load(os.path.join(REPO, "data", "feed", "screener_latest.json"), {})
    cand = (feed or {}).get("candidates", {}) or {}
    for strat in ("close", "swing"):
        items = ((cand.get(strat) or {}).get("items")) or []
        sc = sorted(float(i.get("score", 0.0)) for i in items)
        out[strat] = {"n": len(sc), "max": (sc[-1] if sc else None),
                      "median": (sc[len(sc) // 2] if sc else None),
                      "score_kind": sorted({str(i.get("score_kind")) for i in items}),
                      "generated_at": feed.get("generated_at")}
    # 스크리너 최신 산출물(JSON 형식) — batch_type 판정
    for strat, prefix in (("close", "close_candidates_"), ("swing", "swing_candidates_")):
        files = sorted(glob.glob(os.path.join(REPO, "data", "reports", f"{prefix}*.csv")))
        if not files:
            out[strat]["batch_type"] = None
            continue
        d = load(files[-1], {}) or {}
        out[strat].update({"artifact": os.path.basename(files[-1]),
                           "batch_type": d.get("batch_type"), "up": d.get("up"),
                           "total": d.get("total"), "auc": d.get("auc")})
    return out


def ledger_scan(n=40):
    """최근 원장 기록의 per_exp 오용(arm 아닌 실측을 per_exp 로 싣기) 스캔."""
    hits = []
    p = os.path.join(REPO, "data", "reports", "model_engineer_ledger.jsonl")
    if not os.path.exists(p):
        return hits
    rows = [json.loads(l) for l in open(p, encoding="utf-8", errors="replace") if l.strip()][-n:]
    for r in rows:
        parsed = r.get("parsed") or {}
        if "per_exp" in parsed and (r.get("metric") or "").endswith("eval"):
            hits.append({"ts": r.get("ts"), "id": r.get("id"), "metric": r.get("metric")})
    return hits


def main():
    now = dt.datetime.now()
    os.makedirs(OUT_DIR, exist_ok=True)
    rec = {"ts": now.isoformat(timespec="seconds"), "mode": "measure", "issues": [], "info": {}}
    champ = champion_info()
    scores = live_scores()
    rec["info"] = {"champion": champ, "live_scores": scores}

    # 1) 라이브 스코어가 살아있나 (MT116 재발 감지)
    sw = scores.get("swing", {}) or {}
    bt, up, mx = sw.get("batch_type"), sw.get("up"), sw.get("max")
    if bt is not None and bt != "signal":
        rec["issues"].append({"check": "live_score_path_closed",
                              "detail": f"스윙 배치 {bt} (up={up}/{sw.get('total')}, max={mx}) — "
                                        f"0.55 초과 신호 0건이면 소비자가 배치를 거부한다. "
                                        f"재현: scripts/_swing_ensemble_weight_probe.py"})
    if isinstance(mx, float) and mx < CONF_TS:
        rec["issues"].append({"check": "swing_max_below_threshold",
                              "detail": f"최신 swing 피드 최대 confidence {mx} < {CONF_TS}"})

    # 2) champion vs 백업 (승격 직후 회귀 감지) — 비교는 robust_auc 우선, 백업은 최신 2개만.
    #    auc.txt 만 비교하면 레거시 단일분할 운값(0.6131 동결분)이 오탐을 만든다.
    auc = champ.get("auc")
    robust_now = (champ.get("robust") or {}).get("robust_auc")
    backups = sorted(glob.glob(os.path.join(REL, "champion_prev_*")),
                     key=lambda p: os.path.getmtime(p), reverse=True)[:2]
    rec["info"]["prev_aucs"] = {}
    for b in backups:
        rb = load(os.path.join(b, "robust_auc.json"), {}) or {}
        pa = rb.get("robust_auc")
        if pa is None:
            try:
                pa = float(open(os.path.join(b, "auc.txt")).read().strip())
            except Exception:  # noqa: BLE001
                pa = None
        rec["info"]["prev_aucs"][os.path.basename(b)] = pa
        try:
            if pa is not None and robust_now is not None and float(pa) - float(robust_now) > 0.01:
                rec["issues"].append({"check": "champion_regressed_vs_backup",
                                      "detail": f"현 챔피언 {robust_now} < 직전 백업 {os.path.basename(b)} {pa}"})
        except Exception:  # noqa: BLE001
            pass

    # 3) 서브모델 AUC 격차 (배포 균등평균 ≠ 검증 가중평균 위험)
    ma = (champ.get("robust") or {}).get("model_aucs") or {}
    if len(ma) >= 2:
        spread = max(ma.values()) - min(ma.values())
        rec["info"]["submodel_auc_spread"] = round(spread, 4)
        if spread > SUB_AUC_SPREAD_WARN:
            rec["issues"].append({"check": "submodel_auc_spread",
                                  "detail": f"서브모델 AUC 격차 {spread:.4f} > {SUB_AUC_SPREAD_WARN} — "
                                            f"배포는 균등평균(val_weights 미복원), 검증값은 가중평균이라 "
                                            f"라이브 스코어가 검증값과 다르다 {ma}"})

    # 4) 원장 per_exp 오용
    hits = ledger_scan()
    if hits:
        rec["issues"].append({"check": "per_exp_misuse",
                              "detail": f"실측(비-arm) 기록에 per_exp 존재 {len(hits)}건 — 스코어보드 오독 위험: {hits[:3]}"})

    # 5) 유니버스 결정성 (컨테이너 필요 — 미검증 표기)
    rec["info"]["universe_determinism"] = ("미검증 — 컨테이너에서 "
                                           "docker exec -e PYTHONPATH=/app stock_xgboost_ml python "
                                           "/app/scripts/_universe_determinism_test.py")

    rec["status"] = "issues" if rec["issues"] else "ok"
    path = os.path.join(OUT_DIR, f"measure_{now.date().isoformat()}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=1)
    if rec["issues"]:
        print(f"[audit-measure] 이상 {len(rec['issues'])}건 · {path}")
        for i in rec["issues"]:
            print(f"  - {i['check']}: {i['detail']}")
    return 2 if rec["issues"] else 0


if __name__ == "__main__":
    sys.exit(main())
