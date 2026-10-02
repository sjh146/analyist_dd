#!/usr/bin/env python3
"""승격 전 **라이브 스코어 게이트** — 후보가 실제로 신호를 내는가 (MT116 구조 수리).

파이프라인 승격 단계 직전에 호출한다. 후보와 현 챔피언을 **같은 유니버스·같은 피처행렬**에서
채점하는 프로브(`scripts/_swing_ensemble_weight_probe.py`)를 돌려, 후보의 절대문턱 초과 신호가
**0건이면 승격을 차단**(exit 2)한다.

왜 AUC 만으로 부족한가 (2026-10-02 실측 MT116): 10-01 승격은 val AUC +0.0035 였지만 배포 스코어
분포가 0.55 아래로 내려가 swing `batch_type` 이 signal→raw_fallback 으로 뒤집혔고, 소비자가 배치를
전량 거부해 3세션 무진입이 됐다. AUC 게이트(0.02)는 그런 '경로 닫힘'을 볼 수 없다.

사용:
  python3 scripts/gate_promote_live_score.py --candidate app/models/champion_cand
종료코드: 0 허용(신호 ≥1건) / 2 차단(신호 0건) / 3 측정 실패(보수적으로 **차단하지 않음** — 로그에 남기고 AUC 게이트에 맡긴다).
"""
import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys

REPO = "/home/jhshi/analyist_dd"
OUT = os.path.join(REPO, "data", "reports", "promote_live_score_gate.json")
# 승격 초크포인트(app/training/champion_promote.py)가 요구하는 토큰 — 컨테이너와 공유되는 경로.
# (services/xgboost-ml -> /app 바인드 → 컨테이너에서는 /app/app/models/promote_live_score_gate.json)
TOKEN = os.path.join(REPO, "services", "xgboost-ml", "app", "models", "promote_live_score_gate.json")


def write_token(candidate: str, status: str, detail: str, **extra) -> None:
    """승격이 이 게이트를 **반드시 통과**하게 하는 토큰을 남긴다.

    WHY: 파이프라인은 이 스크립트를 먼저 돌리지만 역할 틱·수동 승격은 건너뛸 수 있다.
    토큰이 없거나 passed 가 아니면 champion_promote 가 승격을 거부한다(MT116 재발 방지).
    """
    rec = {"candidate": candidate, "status": status, "detail": detail,
           "ts": dt.datetime.now().isoformat(timespec="seconds"), **extra}
    try:
        os.makedirs(os.path.dirname(TOKEN), exist_ok=True)
        with open(TOKEN, "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False, indent=1)
    except OSError as e:  # 토큰을 못 쓰면 승격이 거부된다 — 조용히 넘기지 않는다
        print(f"[gate-live-score] 경고: 토큰 기록 실패({e!r}) → 승격 초크포인트가 거부한다")


def probe(candidate: str, timeout: int = 900):
    env_dirs = json.dumps([["candidate", candidate], ["champion", "/app/app/models/champion"]])
    cmd = ["docker", "exec", "-e", "PYTHONPATH=/app", "-e", f"PROBE_MODEL_DIRS={env_dirs}",
           "stock_xgboost_ml", "python", "/app/scripts/_swing_ensemble_weight_probe.py"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=REPO)
        out = (r.stdout or "") + (r.stderr or "")
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"
    m = re.search(r"^PROBE_SUMMARY (\{.*\})$", out, re.M)
    if not m:
        return None, "\n".join(out.strip().splitlines()[-4:])
    try:
        return json.loads(m.group(1)), None
    except json.JSONDecodeError as e:
        return None, f"요약 파싱 실패: {e}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidate", required=True, help="컨테이너 경로 (예: app/models/champion_cand)")
    ap.add_argument("--conf", type=float, default=0.55, help="신호 문턱(스크리너 batch_type 과 동일)")
    ap.add_argument("--min-signals", type=int, default=1)
    a = ap.parse_args()

    rec = {"ts": dt.datetime.now().isoformat(timespec="seconds"), "candidate": a.candidate,
           "conf": a.conf, "min_signals": a.min_signals}
    summary, err = probe(a.candidate)
    if summary is None:
        rec.update({"verdict": "측정실패", "detail": err})
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        json.dump(rec, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        write_token(a.candidate, "measure_failed", f"프로브 실패: {err}")
        print(f"[gate-live-score] 측정 실패 — AUC 게이트에 위임: {err} "
              f"(토큰=measure_failed → 승격 초크포인트는 거부한다: 증거 없는 승격 금지)")
        return 3

    cand = summary.get("candidate") or {}
    champ = summary.get("champion") or {}
    n_cand = int(cand.get("deployed_gt_conf", 0))
    n_champ = int(champ.get("deployed_gt_conf", 0))
    rec.update({"candidate_signals": n_cand, "champion_signals": n_champ,
                "candidate_max": cand.get("deployed_max"), "champion_max": champ.get("deployed_max"),
                "n_universe": cand.get("n")})
    blocked = n_cand < a.min_signals
    rec["verdict"] = "차단" if blocked else "허용"
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(rec, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    write_token(a.candidate, "blocked" if blocked else "passed",
                f"후보 신호 {n_cand}건(문턱 {a.conf} 초과, 챔피언 {n_champ}건, 최대 {cand.get('deployed_max')})",
                candidate_signals=n_cand, champion_signals=n_champ,
                candidate_max=cand.get("deployed_max"))
    if blocked:
        print(f"[gate-live-score] 차단: 후보 신호 {n_cand}건(문턱 {a.conf} 초과) · 최대 {cand.get('deployed_max')} "
              f"— 소비자가 배치를 거부한다(MT116 유형). 챔피언 신호 {n_champ}건.")
        return 2
    print(f"[gate-live-score] 허용: 후보 신호 {n_cand}건 · 최대 {cand.get('deployed_max')} "
          f"(챔피언 {n_champ}건)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
