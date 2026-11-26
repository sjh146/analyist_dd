#!/usr/bin/env python3
"""rollback_evidence_refresh.py — 되돌림 감시의 증거를 **같은 프로토콜로 다시 잰다**.

WHY (2026-11-26 실측): `scripts/champion_rollback_monitor.py` 는 챔피언·직전 챔피언의
`robust_oos.json`(승격 시점 1회 기록)을 비교하는데, 그 파일은 이후 갱신되지 않는다.
마지막 스왑이 2026-10-02 이므로 챔피언 증거가 14일(max_evidence_age_days)을 넘겨
2026-11-09 이후 **매일 `stale_evidence`** 로 판정 불가가 됐다(자동 원복 게이트 무력).
임계값을 늘리는 것은 해법이 아니다(증거가 낡은 채로 계속 'ok' 가 된다).

해법: 두 모델을 **같은 표본·같은 topk·같은 수수료**로 다시 재서 짝 증거를 만든다.
생산기는 이미 있다 — `scripts/_expectancy_is_oos_split.py` 가 `--models` 로 여러 모델을
받아 모델별 train_end 이후(OOS) 세션 기대를 낸다. 여기서는 그것을 돌려 계약 파일로 정규화한다.

출력 `data/reports/rollback_evidence.json`:
  {"created_at": ..., "protocol": {...}, "sample": {...},
   "models": {"champion": {"expectancy_pct","n_sessions","t_stat","train_end","halves"}, "<prev_dir>": {...}}}

감시기는 이 파일이 (a) 두 모델을 모두 담고 (b) max_age_days 이내일 때만 비교에 쓴다.
한쪽만 있으면 프로토콜이 섞이므로 쓰지 않고 종전 경로로 폴백한다.

사용:
  python3 scripts/rollback_evidence_refresh.py            # 측정 + 기록
  python3 scripts/rollback_evidence_refresh.py --dry-run  # 측정만(파일 안 씀)
  python3 scripts/rollback_evidence_refresh.py --from-raw services/xgboost-ml/reports/overnight/rollback_evidence_raw.json

함정(실측 2026-11-26): 생산기 `_expectancy_is_oos_split.py` 는 파일을 다 쓴 뒤에도 **프로세스가
끝나지 않는다**(그래프 드라이버 스레드가 남아 docker exec 가 반환되지 않는다 — 30분 timeout 까지 CPU 를
잡고 있었다). 그래서 여기서는 ① 산출 파일의 mtime 이 실행 시작 이후면 timeout 이어도 채택하고
② 이미 측정된 산출물은 --from-raw 로 재사용한다(재측정 금지 — CPU 직렬화).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SWAPS = os.path.join(REPO, "data", "reports", "champion_swaps.jsonl")
RAW_HOST = os.path.join(REPO, "services", "xgboost-ml", "reports", "overnight", "rollback_evidence_raw.json")
RAW_CT = "/app/reports/overnight/rollback_evidence_raw.json"
OUT = os.path.join(REPO, "data", "reports", "rollback_evidence.json")
CONTAINER = "stock_xgboost_ml"


def last_swap_prev(path: str = SWAPS):
    try:
        lines = [l for l in open(path, encoding="utf-8") if l.strip()]
    except OSError:
        return None
    for line in reversed(lines):
        d = None
        try:
            d = json.loads(line)
        except ValueError:
            d = None
        if isinstance(d, dict) and d.get("prev_dir"):
            return str(d["prev_dir"])
    return None


def normalize(raw: dict, prev_dir: str, now: dt.datetime | None = None) -> dict:
    models = {}
    ca = raw.get("created_at") or (now or dt.datetime.now()).isoformat(timespec="seconds")
    for name, block in (raw.get("models") or {}).items():
        oos = (block or {}).get("oos") or {}
        models[name] = {
            "expectancy_pct": oos.get("avg_pct"),
            "n_sessions": oos.get("n_sessions"),
            "median_pct": oos.get("median_pct"),
            "t_stat": oos.get("t_stat"),
            "halves": oos.get("halves"),
            "train_end": (block or {}).get("train_end"),
        }
    return {
        "created_at": ca,
        "normalized_at": (now or dt.datetime.now()).isoformat(timespec="seconds"),
        "source": "scripts/_expectancy_is_oos_split.py (동일 trades 표본 · OOS=모델 train_end 이후)",
        "prev_dir": prev_dir,
        "sample": raw.get("sample") or {},
        "models": models,
    }


def _finish(raw: dict, prev_dir: str, dry_run: bool, source: str = RAW_CT) -> int:
    out = normalize(raw, prev_dir)
    out["raw_source"] = source
    missing = [m for m in ("champion", prev_dir) if not (out["models"].get(m) or {}).get("n_sessions")]
    for name, m in out["models"].items():
        print("  %-30s OOS %s%%p n=%s t=%s (train_end %s)"
              % (name, m["expectancy_pct"], m["n_sessions"], m["t_stat"], m["train_end"]))
    if missing:
        print(f"[x] OOS 세션 0 인 모델: {missing} — 증거로 쓰지 않는다", file=sys.stderr)
        return 1
    if dry_run:
        print("[i] dry-run — 파일 미기록")
        return 0
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    os.replace(tmp, OUT)
    print("[ok] 기록:", OUT)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="되돌림 감시 증거 재측정(챔피언 vs 직전 챔피언)")
    ap.add_argument("--prev-dir", default=None, help="기본: champion_swaps.jsonl 마지막 prev_dir")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--from-raw", default=None,
                    help="이미 측정된 산출 JSON 을 정규화만 한다(생산기 재실행 금지)")
    a = ap.parse_args(argv)

    prev_dir = a.prev_dir or last_swap_prev()
    if not prev_dir:
        print("[x] 직전 챔피언(prev_dir)을 알 수 없다 — champion_swaps.jsonl 확인", file=sys.stderr)
        return 2
    if a.from_raw:
        path = a.from_raw if os.path.isabs(a.from_raw) else os.path.join(REPO, a.from_raw)
        raw = json.load(open(path, encoding="utf-8"))
        return _finish(raw, prev_dir, a.dry_run, source=path)

    spec = [["champion", "app/models/champion"], [prev_dir, f"app/models/{prev_dir}"]]
    inner = ("cd /app && OMP_NUM_THREADS=2 timeout %d python -u scripts/_expectancy_is_oos_split.py "
             "--models '%s' --dump-sessions --json-out %s"
             % (a.timeout, json.dumps(spec), RAW_CT))
    cmd = ["docker", "exec", CONTAINER, "sh", "-c", inner]
    print("[i] 측정:", " ".join(cmd))
    started = dt.datetime.now().timestamp()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=a.timeout + 60)
        rc, out, err = p.returncode, p.stdout or "", p.stderr or ""
    except subprocess.TimeoutExpired as e:
        rc, out, err = 124, (e.stdout or "") if isinstance(e.stdout, str) else "", "timeout(산출 후 미종료)"
    sys.stdout.write(out[-3000:])
    fresh = os.path.exists(RAW_HOST) and os.path.getmtime(RAW_HOST) >= started - 5
    if rc != 0:
        print(f"[!] 생산기 rc={rc} {err[-300:]}")
        if not fresh:
            print("[x] 산출 파일이 갱신되지 않았다 — 증거로 쓰지 않는다", file=sys.stderr)
            return 1
        print("[i] 그래도 산출 파일이 실행 시작 이후 갱신됨 → 채택(종료코드는 생산기 특성)")
    raw = json.load(open(RAW_HOST, encoding="utf-8"))
    return _finish(raw, prev_dir, a.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
