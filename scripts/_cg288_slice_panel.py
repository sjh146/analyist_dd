#!/usr/bin/env python3
"""CG288 준비 — SNS 관측 구간만 남긴 '창 축소' 패널을 만든다(**가산 아님·행 부분집합**).

왜(실측 근거, 2026-11-15 18:47 엔지니어):
  CG241/CG282 실측: panel_200liq_ffill.npz 의 SNS 값은 2026-05-27 이후에만 관측된다
  (관측행 7,644/54,497 = 14.0% → 슬라이스 안에서는 29.7%). 5폴드 확장창에서는 fold1·fold2
  학습창 관측률이 0% 라 std=0 필터가 ffill_* 4종을 전부 탈락시켰고, 하네스가 측정 전에 죽었다.
  즉 '모델이 못 쓴다'가 아니라 '창이 데이터 사정과 안 맞는다' → 창을 관측 시작 이후로 줄이면
  축소 프로토콜로나마 A/B 가 성립한다.

무엇을 하나:
  panel_200liq_ffill.npz 에서 date >= CUTOFF('2026-05-27') 행만 남겨
  panel_200liq_ffill_2605.npz 로 저장한다. 원본은 **읽기만** 한다(불변).
  키(X·feature_names·dates·codes·price)를 전부 보존하고 행 정렬을 유지한다.

한계(정직하게):
  - 절단으로 각 종목의 초기 이력이 사라진다 → 라벨 스무딩(h5 + 1~5일 평균)이 슬라이스 앞쪽
    며칠에서 결측이 된다. 폴드1 학습행이 줄어드는 것은 정상이며 spec 에 기록한다.
  - 112거래일 · 2폴드 → 표본이 작다. 판정은 '축 재개 여부' 결정용이지 승격 근거가 아니다.

사용:
  python3 scripts/_cg288_slice_panel.py           # 빌드 + 자기검정
  python3 scripts/_cg288_slice_panel.py --test    # 자기검정만
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

import numpy as np

SRC = "/app/app/models/wf/panel_200liq_ffill.npz"
OUT = "/app/app/models/wf/panel_200liq_ffill_2605.npz"
CUTOFF = "2026-05-27"
META = "/app/reports/overnight/cg288_slice_meta.json"


def _digest(a: np.ndarray) -> str:
    h = hashlib.sha256()
    h.update(np.ascontiguousarray(a).tobytes())
    return h.hexdigest()[:16]


def build(src: str = SRC, out: str = OUT, cutoff: str = CUTOFF) -> dict:
    if not os.path.exists(src):
        raise SystemExit(f"원본 패널 없음: {src}")
    z = np.load(src, allow_pickle=True)
    X = np.asarray(z["X"], dtype=np.float32)
    names = np.asarray(z["feature_names"])
    dates = np.asarray(z["dates"]).astype(str)
    codes = np.asarray(z["codes"]).astype(str)
    price = np.asarray(z["price"]) if "price" in z else None
    if not (len(X) == len(dates) == len(codes)):
        raise SystemExit(f"행 길이 불일치: X={len(X)} dates={len(dates)} codes={len(codes)}")
    if price is not None and len(price) != len(dates):
        raise SystemExit("price 행 길이 불일치")

    keep = dates >= cutoff
    if not keep.any():
        raise SystemExit(f"절단 후 행이 0개 (cutoff={cutoff})")
    sl = {
        "X": X[keep],
        "feature_names": names,
        "dates": dates[keep],
        "codes": codes[keep],
    }
    if price is not None:
        sl["price"] = np.asarray(price)[keep]
    np.savez_compressed(out, **sl)

    ud = np.unique(sl["dates"])
    meta = {
        "src": src, "out": out, "cutoff": cutoff,
        "src_rows": int(len(X)), "out_rows": int(keep.sum()),
        "src_dates": int(len(np.unique(dates))), "out_dates": int(len(ud)),
        "out_from": min(ud.tolist()), "out_to": max(ud.tolist()),
        "codes": int(len(np.unique(sl["codes"]))), "n_features": int(X.shape[1]),
        "src_sha_X_first_row": _digest(X[0]), "out_sha_X_first_row": _digest(sl["X"][0]),
        "built_at": __import__("datetime").datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    os.makedirs(os.path.dirname(META), exist_ok=True)
    with open(META, "w") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    return meta


def test(src: str = SRC, out: str = OUT, cutoff: str = CUTOFF) -> int:
    """자기검정 — 절단이 '행 부분집합'임을 비트 수준으로 확인한다."""
    zs = np.load(src, allow_pickle=True)
    zo = np.load(out, allow_pickle=True)
    Xs = np.asarray(zs["X"], dtype=np.float32)
    Xo = np.asarray(zo["X"], dtype=np.float32)
    ns = [str(x) for x in zs["feature_names"]]
    no = [str(x) for x in zo["feature_names"]]
    ds = np.asarray(zs["dates"]).astype(str)
    do = np.asarray(zo["dates"]).astype(str)
    cs = np.asarray(zs["codes"]).astype(str)
    co = np.asarray(zo["codes"]).astype(str)
    ps = np.asarray(zs["price"]) if "price" in zs else None
    po = np.asarray(zo["price"]) if "price" in zo else None

    keys_src = (ds + "|" + cs)
    keys_out = (do + "|" + co)
    pos = {k: i for i, k in enumerate(keys_src)}

    checks = []

    def ck(name, cond, detail=""):
        checks.append((name, bool(cond), detail))

    ck("1 키 보존(feature_names 동일·순서 동일)", ns == no,
       f"{len(ns)} vs {len(no)}")
    ck("2 행 길이 정합", len(Xo) == len(do) == len(co) and (po is None or len(po) == len(do)),
       f"X={len(Xo)} dates={len(do)} codes={len(co)}")
    ck("3 절단 준수(min(dates) >= cutoff)", min(do.tolist()) >= cutoff,
       f"min={min(do.tolist())} cutoff={cutoff}")
    exp_rows = int((ds >= cutoff).sum())
    ck("4 누락 없음(원본의 cutoff 이후 행 수와 일치)", len(do) == exp_rows,
       f"out={len(do)} expected={exp_rows}")
    miss = [k for k in keys_out if k not in pos]
    ck("5 모든 출력 키가 원본에 존재", not miss, f"missing={len(miss)}")
    # 비트 동일 표본 검사(무작위 40행 + 첫·끝 행)
    rng = np.random.default_rng(0)
    idx = list(rng.choice(len(do), size=min(40, len(do)), replace=False))
    idx = sorted(set(idx) | {0, len(do) - 1})
    bad_X = [i for i in idx if not np.array_equal(Xo[i], Xs[pos[keys_out[i]]], equal_nan=True)]
    ck("6 표본 X 행 비트 동일", not bad_X, f"checked={len(idx)} bad={len(bad_X)}")
    if ps is not None and po is not None:
        bad_p = [i for i in idx
                 if not np.array_equal(np.asarray(po[i], dtype=float),
                                       np.asarray(ps[pos[keys_out[i]]], dtype=float),
                                       equal_nan=True)]
        ck("7 표본 price 비트 동일", not bad_p, f"checked={len(idx)} bad={len(bad_p)}")
    else:
        ck("7 price 키 존재", False, "src/out 중 price 없음")

    ok = all(c[1] for c in checks)
    for n, c, d in checks:
        print(("PASS " if c else "FAIL ") + n + (f" — {d}" if d else ""))
    print(("자기검정 전부 PASS" if ok else "자기검정 실패") + f" ({sum(1 for c in checks if c[1])}/{len(checks)})")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--src", default=SRC)
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--cutoff", default=CUTOFF)
    a = ap.parse_args()
    if a.test:
        return test(a.src, a.out, a.cutoff)
    build(a.src, a.out, a.cutoff)
    return test(a.src, a.out, a.cutoff)


if __name__ == "__main__":
    sys.exit(main())
