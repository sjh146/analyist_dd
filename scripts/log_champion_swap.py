#!/usr/bin/env python3
"""챔피언 교체/롤백을 릴리스 로그에 기록한다 (트레이더 환류 MT117).

배경: 2026-10-02 11:44 롤백(0.554776 → 0.551318)이 어디에도 기록되지 않아 트레이더가
디렉터리 mtime 으로 역추적해야 했다. 저녁 파이프라인의 승격 단계 뒤에서 이 스크립트가
매번 호출되면 교체·롤백이 `data/reports/champion_swaps.jsonl` 에 한 줄씩 남는다.

성질: 읽기 전용 조회 + append 1줄(멱등 — 같은 (auc, prev_dir, prev_auc, recorded_at) 은 스킵).
실패해도 파이프라인을 막지 않는다(호출부에서 `|| true`).
"""
import datetime as dt
import glob
import json
import os

REL = "/home/jhshi/analyist_dd/services/xgboost-ml/app/models"
OUT = "/home/jhshi/analyist_dd/data/reports/champion_swaps.jsonl"


def read_text(p):
    try:
        return open(p, encoding="utf-8").read().strip()
    except OSError:
        return None


def key_of(rec):
    return json.dumps([rec.get("champion_auc"), rec.get("prev_dir"), rec.get("prev_auc"),
                       (rec.get("robust") or {}).get("recorded_at")], ensure_ascii=False)


def main():
    champ = os.path.join(REL, "champion")
    auc = read_text(os.path.join(champ, "auc.txt"))
    try:
        rob = json.load(open(os.path.join(champ, "robust_auc.json"), encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        rob = {}
    prevs = sorted(glob.glob(os.path.join(REL, "champion_prev_*")), key=os.path.getmtime, reverse=True)
    prev = prevs[0] if prevs else None
    rec = {
        "ts": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "champion_auc": auc,
        "prev_dir": os.path.basename(prev) if prev else None,
        "prev_auc": read_text(os.path.join(prev, "auc.txt")) if prev else None,
        "robust": {k: rob.get(k) for k in ("robust_auc", "recorded_at", "replaced_baseline", "replaced_source")},
        "model_aucs": rob.get("model_aucs"),
        "n_features": (lambda p: (len(json.load(open(p, encoding="utf-8")))
                                  if os.path.exists(p) else None))(os.path.join(champ, "feature_names.json")),
    }
    seen = set()
    if os.path.exists(OUT):
        for line in open(OUT, encoding="utf-8"):
            try:
                seen.add(key_of(json.loads(line)))
            except Exception:  # noqa: BLE001
                continue
    if key_of(rec) in seen:
        print("[champion-swap] 이미 기록됨 — 스킵")
        return 0
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"[champion-swap] 기록: auc={auc} · prev={rec['prev_dir']}({rec['prev_auc']}) · "
          f"replaced_baseline={rec['robust'].get('replaced_baseline')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
