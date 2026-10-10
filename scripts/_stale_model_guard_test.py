#!/usr/bin/env python3
"""승격≠재기동 가드(CG163) 자체점검 — 2026-10-11 엔지니어 자율.

가드: `app/inference/day_guard.py::check_and_report_stale_model`
배선: `app/main.py::initialize`(로드 시각 기록) · `run_predictions`(발행 직전 호출)

무엇을 증명하는가:
  ① champion 산출물 최신 mtime 추출(모델 신원 = xgboost_model.pkl · feature_names.json)
  ② 판정 규칙: 디스크 champion 이 로드 시각 **이후**면 stale(여유 tolerance), 아니면 정상
  ③ 감지 시에만 증거 파일 `stale_model_<날짜>.json` 이 생기고 **입력·모델·발행을 바꾸지 않는다**
  ④ 실측 회귀: 2026-10-02 승격(02:44) vs 프로세스 기동(02:31) = stale True;
     2026-10-09 재기동(18:22 KST) vs champion mtime(10-05) = stale False (복구일)
  ⑤ 배선 가드: main.py 가 가드를 실제로 호출하고 로드 mtime 을 넘긴다

재현:
  docker exec stock_xgboost_ml sh -c 'cd /app && python -u scripts/_stale_model_guard_test.py'
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
for _cand in (os.path.dirname(_HERE), os.path.join(os.path.dirname(_HERE), "services", "xgboost-ml")):
    if _cand not in sys.path:
        sys.path.insert(0, _cand)

from app.inference.day_guard import (  # noqa: E402
    CHAMPION_ARTIFACTS,
    STALE_MODEL_FILE_PREFIX,
    champion_newest_mtime,
    check_and_report_stale_model,
    stale_model_reason,
)

_PASS = 0
_FAIL: list[str] = []


def ok(cond, msg):
    global _PASS
    if cond:
        _PASS += 1
    else:
        _FAIL.append(msg)


def _mk_champion(base: str, model_mtime: float, names_mtime: float) -> str:
    d = os.path.join(base, "champion")
    os.makedirs(d, exist_ok=True)
    for name, mt in (("xgboost_model.pkl", model_mtime), ("feature_names.json", names_mtime)):
        p = os.path.join(d, name)
        with open(p, "w") as f:
            f.write("x")
        os.utime(p, (mt, mt))
    return d


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="cg163_")
    try:
        # ── ① champion_newest_mtime ────────────────────────────────────────────
        d = _mk_champion(tmp, 1000.0, 1200.0)
        ok(abs(champion_newest_mtime(d) - 1200.0) < 1e-6, "최신 mtime = 두 산출물의 max")
        empty = os.path.join(tmp, "empty")
        os.makedirs(empty)
        ok(champion_newest_mtime(empty) is None, "산출물 없음 → None")
        ok(champion_newest_mtime(os.path.join(tmp, "nope")) is None, "디렉터리 없음 → None(예외 없음)")
        ok(CHAMPION_ARTIFACTS == ("xgboost_model.pkl", "feature_names.json"),
           "모델 신원 = pkl + feature_names (robust_auc 제외)")

        # ── ② stale_model_reason ──────────────────────────────────────────────
        ok(stale_model_reason(1200.0, 1000.0) == "champion_newer_than_process",
           "champion 이 로드 이후 → stale")
        ok(stale_model_reason(1000.0, 1200.0) is None, "champion 이 로드 이전 → 정상")
        ok(stale_model_reason(1000.0, 1000.0) is None, "같은 시각 → 정상")
        ok(stale_model_reason(1000.0 + 4.0, 1000.0, tolerance_s=5.0) is None,
           "여유 5s 이내 → 정상(기동~로드 지연 오탐 방지)")
        ok(stale_model_reason(1000.0 + 6.0, 1000.0, tolerance_s=5.0) == "champion_newer_than_process",
           "여유 5s 초과 → stale")
        ok(stale_model_reason(None, 1000.0) is None, "산출물 mtime 없음 → 판정 보류")
        ok(stale_model_reason(1000.0, None) is None, "로드 시각 없음 → 판정 보류")

        # ── ③ 감지 시에만 증거 파일, 입력 불변 ────────────────────────────────
        rep = os.path.join(tmp, "reports")
        stale_dir = _mk_champion(tmp, 5000.0, 5000.0)
        out = check_and_report_stale_model(stale_dir, loaded_mtime=1000.0,
                                           date="2026-10-02", report_dir=rep)
        ok(out["stale"] is True, "stale 판정 True")
        ok(out["reason"] == "champion_newer_than_process", "사유 기록")
        ok(bool(out["report_path"]) and os.path.exists(out["report_path"]),
           "증거 파일 생성")
        ok(os.path.basename(out["report_path"]) == f"{STALE_MODEL_FILE_PREFIX}2026-10-02.json",
           "파일명 규약 stale_model_<날짜>.json")
        ok(out["champion_mtime"] == 5000.0 and out["loaded_mtime"] == 1000.0,
           "증거에 양쪽 mtime 기록")
        before = sorted(os.listdir(stale_dir))
        out2 = check_and_report_stale_model(stale_dir, loaded_mtime=1000.0, date="2026-10-02")
        ok(before == sorted(os.listdir(stale_dir)), "감지가 champion 디렉터리를 바꾸지 않는다")
        ok(out2["champion_mtime"] == 5000.0, "재호출 결정적")

        fresh_dir = _mk_champion(tmp, 1000.0, 1000.0)
        rep2 = os.path.join(tmp, "reports2")
        out3 = check_and_report_stale_model(fresh_dir, loaded_mtime=2000.0,
                                            date="2026-10-09", report_dir=rep2)
        ok(out3["stale"] is False and out3["report_path"] is None, "정상이면 파일 0")
        ok(not os.path.exists(rep2) or not os.listdir(rep2), "정상일에 증거 파일 없음")

        # ── ④ 실측 회귀(2026-10-02 압축 / 2026-10-09 복구) ────────────────────
        # 10-02 02:44:20 KST 승격 vs 10-02 02:31 기동 → 13분 차 = stale
        _sep = 1780000000.0
        ok(stale_model_reason(_sep + 800.0, _sep) == "champion_newer_than_process",
           "실측 10-02 승격(02:44) > 기동(02:31) → stale")
        # 10-09 09:22Z(=18:22 KST) 기동 vs champion mtime 10-05 → 정상(복구)
        ok(stale_model_reason(_sep - 300000.0, _sep) is None,
           "실측 10-09 재기동 > champion(10-05) → 정상(복구일)")
        # 프로세스 기동 시각을 안 넘기면 모듈 임포트 시각으로 대체된다(예외 없음)
        out4 = check_and_report_stale_model(fresh_dir, date="2026-10-09", report_dir=rep2)
        ok(isinstance(out4, dict) and "stale" in out4, "loaded_mtime 미지정 시 PROCESS_START_TS 대체")
        ok(out4["stale"] is False, "10-09 기준 정상(가드 오탐 0)")

        # ── ⑤ 배선 가드 ───────────────────────────────────────────────────────
        mp = os.path.join(os.path.dirname(_HERE), "services", "xgboost-ml", "app", "main.py")
        src = open(mp, encoding="utf-8").read() if os.path.exists(mp) else ""
        ok("check_and_report_stale_model" in src, "main.py 가 가드를 호출한다")
        ok("champion_newest_mtime(self.config.MODEL_PATH)" in src,
           "main.py 가 로드 시점 champion mtime 을 기록한다")
        ok('loaded_mtime=getattr(self, "_champion_loaded_mtime", None)' in src,
           "main.py 가 로드 mtime 을 가드에 넘긴다")
        ok("stale_model_guard" in open(os.path.join(_HERE, "..", "services", "xgboost-ml",
                                                   "app", "inference", "day_guard.py"),
                                      encoding="utf-8").read(),
           "day_guard 에 metric=stale_model_guard 존재")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"stale_model_guard selftest: {_PASS}/{_PASS + len(_FAIL)} PASS")
    for f in _FAIL:
        print("  FAIL:", f)
    return 0 if not _FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
