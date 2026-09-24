#!/usr/bin/env python3
"""news_wave — 뉴스/감성 데이터가 채워진 뒤의 실험 웨이브 (A/B + 변형 탐색).

기존 드라이버 `scripts/overnight_ml_loop.py` 의 헬퍼(데이터 로드·5시드 학습·
게이트/승격)를 **import 해서 재사용**하고, 그 파일은 수정하지 않는다.

실행(컨테이너, cwd=/app):
    OMP_NUM_THREADS=2 python -u scripts/news_wave.py [--smoke]

규칙:
- 실험은 순차 1개씩, 5시드 평균±std(test AUC) 로 기록.
- 게이트(평균 >= 0.60 AND std <= 0.02) 통과 시 OOS 백테스트 + 승격까지 수행하고 종료.
- 06:15 KST 이후 새 실험 시작 금지.
- 결과는 reports/overnight/news_wave.jsonl 에 append (results.jsonl 은 건드리지 않음).
"""

import argparse
import json
import os
import shutil
import sys
import traceback
from datetime import datetime

APP = "/app"
if APP not in sys.path:
    sys.path.insert(0, APP)
SCRIPTS = os.path.join(APP, "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import overnight_ml_loop as drv  # noqa: E402  (수정 금지, 헬퍼만 사용)

WAVE_DIR = os.path.join(drv.OVERNIGHT_DIR)
WAVE_RESULTS = os.path.join(WAVE_DIR, "news_wave.jsonl")

GATE_MIN_AUC = 0.60
GATE_MAX_STD = 0.02

# before 기준선(뉴스/감성 데이터 없던 상태, 동일 시드·동일 분할)
BEFORE = {"E1": 0.5182, "E2": 0.5205, "E3": 0.5117, "E4": 0.5171,
          "E5": 0.5112, "E7": 0.5188}

# 모든 실험: curated48 = curated43 + sentiment/news 5개 (allow_sentiment=True)
NEWS_WAVE = [
    {"id": "N1", "lr": 0.03, "depth": 4, "n_estimators": 1500,
     "allow_sentiment": True, "scale_pos_weight": None, "horizon": 1,
     "limit": 50, "days": 180, "panel": False,
     "desc": "curated48 (news/sentiment on) lr=0.03 d=4 est=1500 — E5 재측정 (after)"},
    {"id": "N2", "lr": 0.02, "depth": 3, "n_estimators": 2000,
     "allow_sentiment": True, "scale_pos_weight": None, "horizon": 1,
     "limit": 50, "days": 180, "panel": False,
     "desc": "curated48 lr=0.02 d=3 est=2000 (최고 베이스라인 E2 조합 + news)"},
    {"id": "N3", "lr": 0.05, "depth": 5, "n_estimators": 1000,
     "allow_sentiment": True, "scale_pos_weight": None, "horizon": 1,
     "limit": 50, "days": 180, "panel": False,
     "desc": "curated48 lr=0.05 d=5 est=1000"},
    {"id": "N4", "lr": 0.02, "depth": 6, "n_estimators": 1200,
     "allow_sentiment": True, "scale_pos_weight": None, "horizon": 1,
     "limit": 50, "days": 180, "panel": False,
     "desc": "curated48 lr=0.02 d=6 est=1200 (비선형 확대)"},
    {"id": "N5", "lr": 0.01, "depth": 3, "n_estimators": 3000,
     "allow_sentiment": True, "scale_pos_weight": None, "horizon": 1,
     "limit": 50, "days": 180, "panel": False,
     "desc": "curated48 lr=0.01 d=3 est=3000 (저학습률·다트리)"},
    {"id": "N6", "lr": 0.03, "depth": 4, "n_estimators": 1500,
     "allow_sentiment": True, "scale_pos_weight": None, "horizon": 2,
     "limit": 50, "days": 180, "panel": False,
     "desc": "curated48 2일 호라이즌 라벨"},
    {"id": "N7", "lr": 0.03, "depth": 4, "n_estimators": 1500,
     "allow_sentiment": True, "scale_pos_weight": 1.0, "horizon": 1,
     "limit": 50, "days": 180, "panel": False,
     "desc": "curated48 scale_pos_weight=1.0 (오버샘플 대신 가중)"},
    {"id": "N8", "lr": 0.02, "depth": 4, "n_estimators": 2000,
     "allow_sentiment": True, "scale_pos_weight": None, "horizon": 1,
     "limit": 50, "days": 120, "panel": False,
     "desc": "curated48 days=120 (최근 구간만, regime shift 완화)"},
    {"id": "N9", "lr": 0.03, "depth": 3, "n_estimators": 2500,
     "allow_sentiment": True, "scale_pos_weight": None, "horizon": 1,
     "limit": 50, "days": 240, "panel": False,
     "desc": "curated48 days=240 (표본 확대)"},
    {"id": "N10", "lr": 0.04, "depth": 5, "n_estimators": 1500,
     "allow_sentiment": True, "scale_pos_weight": None, "horizon": 1,
     "limit": 50, "days": 180, "panel": False,
     "desc": "curated48 lr=0.04 d=5 est=1500"},
]

# 2차 웨이브: 1차에서 게이트 미통과 시 이어서 실행. 뉴스가 채워진 상태의
# 173피처 패널 경로(=구 챔피언 경로) 재측정을 포함한다.
PHASE2 = [
    {"id": "P1", "lr": 0.025, "depth": 4, "n_estimators": 1800,
     "allow_sentiment": True, "scale_pos_weight": None, "horizon": 1,
     "limit": 50, "days": 150, "panel": False,
     "desc": "curated48 lr=0.025 d=4 est=1800 days=150"},
    {"id": "P2", "lr": 0.02, "depth": 4, "n_estimators": 2000,
     "allow_sentiment": True, "scale_pos_weight": None, "horizon": 1,
     "limit": 50, "days": 200, "panel": False,
     "desc": "curated48 lr=0.02 d=4 est=2000 days=200"},
    {"id": "P3", "lr": 0.03, "depth": 4, "n_estimators": 1500,
     "allow_sentiment": True, "scale_pos_weight": None, "horizon": 1,
     "limit": 200, "days": 120, "panel": True,
     "desc": "173피처 패널 재측정 (news 채워진 상태, E6과 동일 설정)"},
    {"id": "P4", "lr": 0.03, "depth": 4, "n_estimators": 1500,
     "allow_sentiment": True, "scale_pos_weight": None, "horizon": 2,
     "limit": 200, "days": 180, "panel": True,
     "desc": "173피처 패널 + 2일 호라이즌"},
    {"id": "P5", "lr": 0.03, "depth": 4, "n_estimators": 1500,
     "allow_sentiment": True, "scale_pos_weight": 1.4, "horizon": 1,
     "limit": 50, "days": 180, "panel": False,
     "desc": "curated48 scale_pos_weight=1.4"},
    {"id": "P6", "lr": 0.03, "depth": 4, "n_estimators": 1500,
     "allow_sentiment": True, "scale_pos_weight": None, "horizon": 3,
     "limit": 50, "days": 240, "panel": False,
     "desc": "curated48 3일 호라이즌 days=240"},
]

LISTS = {"news": NEWS_WAVE, "phase2": PHASE2}


def append_wave(result):
    os.makedirs(WAVE_DIR, exist_ok=True)
    line = {
        "exp": result.get("exp"),
        "desc": result.get("params", {}).get("desc"),
        "seed_aucs": result.get("seed_aucs"),
        "mean": result.get("mean"),
        "std": result.get("std"),
        "n_rows": result.get("n_rows"),
        "n_features": result.get("n_features"),
        "status": result.get("status"),
        "ts": result.get("ts"),
    }
    with open(WAVE_RESULTS, "a") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")


def main():
    ap = argparse.ArgumentParser(description="news/sentiment experiment wave")
    ap.add_argument("--smoke", action="store_true", help="축소 실행(1실험·2시드)")
    ap.add_argument("--only", default="", help="쉼표 구분 실험 id (예: N1,N2)")
    ap.add_argument("--list", default="news", choices=sorted(LISTS.keys()),
                    help="실행할 실험 목록 (news=1차, phase2=2차)")
    ap.add_argument("--seeds", default="", help="쉼표 구분 시드 재정의 (기본 0,1,2,3,4)")
    ap.add_argument("--deadline-hour", type=int, default=6)
    ap.add_argument("--deadline-minute", type=int, default=15)
    args = ap.parse_args()

    chosen = LISTS[args.list]
    if args.smoke:
        exps = [dict(chosen[0])]
        exps[0]["limit"] = 30
        exps[0]["days"] = 60
        exps[0]["n_estimators"] = 60
        exps[0]["desc"] += " [SMOKE]"
        seeds = [0, 1]
        gate_min, gate_max = 0.0, 1.0
    else:
        exps = list(chosen)
        if args.only:
            want = {x.strip() for x in args.only.split(",") if x.strip()}
            exps = [c for c in exps if c["id"] in want]
        seeds = ([int(s) for s in args.seeds.split(",") if s.strip()]
                 if args.seeds else list(drv.DEFAULT_SEEDS))
        gate_min, gate_max = GATE_MIN_AUC, GATE_MAX_STD

    deadline = drv.next_kst_time(args.deadline_hour, args.deadline_minute)
    drv.set_exp_log("news_wave_all")
    drv.log(f"news_wave start. KST={drv.now_kst().isoformat(timespec='seconds')} "
            f"deadline={deadline.isoformat(timespec='seconds')} seeds={seeds} "
            f"exps={[c['id'] for c in exps]}")

    results = []
    best = None
    gate_passed = False
    for cfg in exps:
        if not args.smoke and drv.now_kst() >= deadline:
            drv.log(f"deadline {deadline.isoformat(timespec='seconds')} 도달 → 남은 실험 중단")
            break
        exp_id = cfg["id"]
        out_dir = os.path.join(drv.MODELS_ROOT, "news_wave", exp_id)
        if os.path.exists(out_dir):
            shutil.rmtree(out_dir, ignore_errors=True)
        os.makedirs(out_dir, exist_ok=True)
        drv.set_exp_log(exp_id)
        drv.set_heartbeat("experiment_start", exp_id, str(cfg["desc"]))
        drv.log(f"=== {exp_id}: {cfg['desc']} ===")

        result = {"exp": exp_id,
                  "params": {k: cfg[k] for k in ("desc", "lr", "depth", "n_estimators",
                                                 "limit", "days", "horizon", "panel",
                                                 "scale_pos_weight", "allow_sentiment")},
                  "seed_aucs": {}, "mean": None, "std": None, "oos": None,
                  "ts": drv.now_iso(), "status": "failed", "seeds": seeds}
        try:
            r = drv.run_curated_experiment(cfg, seeds, out_dir)
            result.update(r)
            result["status"] = "ok"
            result["ts"] = drv.now_iso()
            drv.log(f"RESULT {exp_id} mean={r['mean']:.4f} std={r['std']:.4f} "
                    f"n_rows={r['n_rows']} n_features={r['n_features']} "
                    f"seed_aucs={result['seed_aucs']}")
            if exp_id == "N1":
                drv.log(f"BEFORE(E5)={BEFORE['E5']:.4f} → AFTER(N1)={r['mean']:.4f} "
                        f"(delta {r['mean'] - BEFORE['E5']:+.4f})")
        except Exception as e:
            result["error"] = str(e)
            result["ts"] = drv.now_iso()
            drv.log(f"FAIL {exp_id}: {e}")
            drv.log(traceback.format_exc(), raw=True)

        results.append(result)
        append_wave(result)

        if result["status"] == "ok" and result["mean"] is not None:
            if best is None or result["mean"] > best["mean"]:
                best = result
            if result["mean"] >= gate_min and result["std"] <= gate_max and not args.smoke:
                drv.set_heartbeat("gate", exp_id, f"mean={result['mean']:.4f}")
                drv.log(f"GATE PASSED {exp_id}: mean={result['mean']:.4f} std={result['std']:.4f}")
                gate_passed = bool(drv.handle_gate(cfg, out_dir, result))
                result["gate_passed"] = gate_passed
                if gate_passed:
                    break

    summary = {
        "finished_at": drv.now_iso(),
        "gate_passed": gate_passed,
        "best": {"exp": best["exp"], "mean": best["mean"], "std": best["std"]} if best else None,
        "results": [{"exp": r["exp"], "mean": r.get("mean"), "std": r.get("std"),
                     "status": r["status"]} for r in results],
    }
    with open(os.path.join(WAVE_DIR, "news_wave_summary.json"), "w") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    drv.log(f"news_wave done. gate={gate_passed} best={summary['best']}")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
