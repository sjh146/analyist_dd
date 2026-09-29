#!/usr/bin/env python3
"""patch_panel_events_backfill — 기본 패널의 **낡은 event 컬럼**을 ev 패널의 백필본으로 교체한다.

⚠ 이름 주의: `scripts/patch_panel_events.py`(기존)는 **DB 에서 이벤트 컬럼을 append** 해 ev 패널을
만드는 도구다. 이 스크립트는 그 반대로, **이미 만들어진 ev 패널의 백필 컬럼을 기본 패널에 덮어쓰는**
도구다(위치 기준 교체). 목적이 다르므로 파일을 분리했다.

배경(2026-09-29 실측, scripts/_cg29_diag*.py):
  · panel_420_asofpatch.npz 의 event_* 18개는 **2026-04 이전 구간이 전량 0** 이다
    (event_exec_change_5d 월별 nonzero 0.0% × 2025-07~2026-03 → 1.3~3.2% × 2026-04~).
    즉 워크포워드 폴드 1~2(학습창 ≤2026-01)에서는 이 피처군이 분산 0 으로 **탈락**한다.
  · 같은 이름의 백필본이 panel_420_asofpatch_ev.npz 의 **뒤 17컬럼**(idx 210~226)에 있다
    (DART 공시 백필 2025-07~2026-09 커버, event_exec_change_5d 1.5~8.2% 매월).
  · 두 패널은 행·날짜·종목·price 가 동일(np.array_equal 확인)하므로 컬럼만 교체하면
    '같은 행 위 A/B' 가 성립한다(전체 재빌드 1~2시간 절약, 통제 더 깨끗).

동작: 기본 패널의 X 를 그대로 두고, 이름이 일치하는 ev 추가 컬럼만 **위치 기준으로** 덮어쓴다.
다른 컬럼은 건드리지 않는다(해시로 증명). 출력 파일명은 반드시 새 이름(_evfix).

실행:
  docker exec stock_xgboost_ml python3 /app/scripts/patch_panel_events_backfill.py
"""
import hashlib
import json
import os

import numpy as np

BASE = "/app/app/models/wf/panel_420_asofpatch.npz"
EV = "/app/app/models/wf/panel_420_asofpatch_ev.npz"
DST = "/app/app/models/wf/panel_420_asofpatch_evfix.npz"


def main():
    b = np.load(BASE, allow_pickle=True)
    e = np.load(EV, allow_pickle=True)
    bX = np.array(b["X"], dtype=np.float64, copy=True)
    eX = np.asarray(e["X"], dtype=np.float64)
    bn = [str(x) for x in b["feature_names"]]
    en = [str(x) for x in e["feature_names"]]

    assert bX.shape[0] == eX.shape[0] and eX.shape[1] == len(en)
    assert np.array_equal(np.asarray(b["dates"]).astype(str), np.asarray(e["dates"]).astype(str)), \
        "행 정렬 불일치 — 컬럼 교체 불가"
    assert np.array_equal(np.asarray(b["codes"]).astype(str), np.asarray(e["codes"]).astype(str)), \
        "종목 정렬 불일치"

    added = list(range(210, eX.shape[1]))  # ev 패널의 추가 컬럼(위치 기준)
    patched, no_match = [], []
    for j in added:
        name = en[j]
        if name in bn:
            k = bn.index(name)  # 중복 이름은 첫 열(코드의 이름→열 매핑과 동일 규칙)
            before = bX[:, k].copy()
            bX[:, k] = eX[:, j]
            patched.append({"name": name, "base_col": k, "ev_col": j,
                            "changed_rows": int(np.sum(before != bX[:, k])),
                            "nonzero_before_pct": round(100 * float(np.mean(before != 0)), 3),
                            "nonzero_after_pct": round(100 * float(np.mean(bX[:, k] != 0)), 3)})
        else:
            no_match.append(name)
    if not patched:
        raise SystemExit("교체된 컬럼이 0개 — 이름 매핑 확인 필요")

    # 다른 컬럼이 그대로인지 증명: 교체 컬럼을 제외한 나머지의 해시 비교
    keep = np.ones(bX.shape[1], dtype=bool)
    for p in patched:
        keep[p["base_col"]] = False
    h_before = hashlib.sha256(np.ascontiguousarray(bX[:, keep]).tobytes()).hexdigest()  # patched 값 포함
    h_base = hashlib.sha256(np.ascontiguousarray(np.asarray(b["X"], dtype=np.float64)[:, keep]).tobytes()).hexdigest()

    dates = np.asarray(b["dates"]).astype(str)
    months = sorted(set(d[:7] for d in dates))
    reproof = {}
    for p in patched:
        if p["name"] in ("event_exec_change_5d", "event_stake_change_5d", "disclosure_count_5d"):
            reproof[p["name"]] = {
                m: round(100 * float(np.mean(bX[np.char.startswith(dates, m), p["base_col"]] != 0)), 2)
                for m in months}

    np.savez_compressed(DST, X=bX, feature_names=np.asarray(bn, dtype=object),
                        dates=np.asarray(b["dates"]), codes=np.asarray(b["codes"]),
                        price=np.asarray(b["price"]))
    meta = {
        "src_base": BASE, "src_ev": EV, "dst": DST, "n_patched": len(patched),
        "patched": patched, "ev_cols_without_base_name": no_match,
        "non_patched_identical_hash": h_before == h_base,
        "monthly_nonzero_after_pct": reproof,
        "rows": int(bX.shape[0]), "features": int(bX.shape[1]),
        "dst_bytes": os.path.getsize(DST),
    }
    with open("/app/reports/overnight/patch_panel_events_backfill.json", "w") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    print(json.dumps({k: v for k, v in meta.items() if k != "patched"}, ensure_ascii=False, indent=1))
    for p in patched:
        print("  %-30s base_col=%3d changed_rows=%5d nonzero %5.2f%% → %5.2f%%"
              % (p["name"], p["base_col"], p["changed_rows"],
                 p["nonzero_before_pct"], p["nonzero_after_pct"]))


if __name__ == "__main__":
    main()
