#!/usr/bin/env python3
"""model_version 귀속 + 전방 성적표 중복 제거 자체점검 (pytest 없음 — PASS/FAIL + exit code).

배경: ml_predictions.model_version 이 항상 'v1.0'(ML_MODEL_VERSION 기본값)이라 승격/롤백
전후 예측을 구분할 수 없었다(scripts/forward_scorecard.py CG68 한계). 수리:
`app/inference/predictor._resolve_model_version()` 이 배포 챔피언 메타에서 식별자를 만든다.

실행(컨테이너): docker exec -e PYTHONPATH=/app stock_xgboost_ml python /app/scripts/_model_version_attr_test.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, "/app")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))

FAILS = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"   [{detail}]" if detail else ""))
    if not cond:
        FAILS.append(name)


def main():
    from app.inference import predictor
    import forward_scorecard as fs

    def mk_model_dir(tmp, meta=None, auc_txt=None):
        champ = os.path.join(tmp, "champion")
        os.makedirs(champ, exist_ok=True)
        if meta is not None:
            with open(os.path.join(champ, "robust_auc.json"), "w") as f:
                json.dump(meta, f)
        if auc_txt is not None:
            with open(os.path.join(champ, "auc.txt"), "w") as f:
                f.write(auc_txt)
        return tmp

    # 1) robust_auc.json 있음 · env 없음 → champ-<ts>-<auc>
    with tempfile.TemporaryDirectory() as tmp:
        predictor._MODEL_VERSION_CACHE = None
        os.environ.pop("ML_MODEL_VERSION", None)
        mk_model_dir(tmp, meta={"recorded_at": "2026-10-02T02:44:20", "robust_auc": 0.5513})
        got = predictor._resolve_model_version(tmp)
        check("1 meta → champ-<ts>-<auc>", got == "champ-20261002T024420-0.5513", got)

    # 2) env='v1.0'(레거시 자리표시자) → 무시하고 메타 사용
    with tempfile.TemporaryDirectory() as tmp:
        predictor._MODEL_VERSION_CACHE = None
        os.environ["ML_MODEL_VERSION"] = "v1.0"
        mk_model_dir(tmp, meta={"recorded_at": "2026-10-02T02:44:20", "robust_auc": 0.5513})
        got = predictor._resolve_model_version(tmp)
        check("2 env=v1.0 무시 → 메타", got == "champ-20261002T024420-0.5513", got)
        os.environ.pop("ML_MODEL_VERSION", None)

    # 3) env 가 실제 값이면 존중
    with tempfile.TemporaryDirectory() as tmp:
        predictor._MODEL_VERSION_CACHE = None
        os.environ["ML_MODEL_VERSION"] = "custom-v2"
        mk_model_dir(tmp, meta={"recorded_at": "2026-10-02T02:44:20", "robust_auc": 0.5513})
        got = predictor._resolve_model_version(tmp)
        check("3 env 지정 시 존중", got == "custom-v2", got)
        os.environ.pop("ML_MODEL_VERSION", None)

    # 4) 메타 없음 → auc.txt 폴백
    with tempfile.TemporaryDirectory() as tmp:
        predictor._MODEL_VERSION_CACHE = None
        os.environ.pop("ML_MODEL_VERSION", None)
        mk_model_dir(tmp, auc_txt="0.551318\n")
        got = predictor._resolve_model_version(tmp)
        check("4 auc.txt 폴백", got.startswith("champ-") and got.endswith("-0.5513"), got)

    # 5) 아무것도 없음 → v1.0
    with tempfile.TemporaryDirectory() as tmp:
        predictor._MODEL_VERSION_CACHE = None
        os.environ.pop("ML_MODEL_VERSION", None)
        os.makedirs(os.path.join(tmp, "champion"), exist_ok=True)
        got = predictor._resolve_model_version(tmp)
        check("5 자료 없음 → v1.0", got == "v1.0", got)

    # 6) 깨진 JSON → 크래시 없이 폴백
    with tempfile.TemporaryDirectory() as tmp:
        predictor._MODEL_VERSION_CACHE = None
        os.environ.pop("ML_MODEL_VERSION", None)
        champ = os.path.join(tmp, "champion")
        os.makedirs(champ, exist_ok=True)
        with open(os.path.join(champ, "robust_auc.json"), "w") as f:
            f.write("{broken json")
        got = predictor._resolve_model_version(tmp)
        check("6 깨진 메타 → v1.0(크래시 없음)", got == "v1.0", got)

    # 7) 캐시: 파일을 바꿔도 프로세스 내 값 불변
    with tempfile.TemporaryDirectory() as tmp:
        predictor._MODEL_VERSION_CACHE = None
        os.environ.pop("ML_MODEL_VERSION", None)
        mk_model_dir(tmp, meta={"recorded_at": "2026-10-02T02:44:20", "robust_auc": 0.5513})
        first = predictor._resolve_model_version(tmp)
        with open(os.path.join(tmp, "champion", "robust_auc.json"), "w") as f:
            json.dump({"recorded_at": "2026-11-01T00:00:00", "robust_auc": 0.6}, f)
        second = predictor._resolve_model_version(tmp)
        check("7 프로세스 내 캐시", first == second == "champ-20261002T024420-0.5513", f"{first}/{second}")

    # 8) 실제 배포 챔피언 → champ- 접두 (통합)
    predictor._MODEL_VERSION_CACHE = None
    os.environ.pop("ML_MODEL_VERSION", None)
    real = predictor._resolve_model_version()
    check("8 실제 챔피언 해석", real.startswith("champ-") or real == "v1.0", real)

    # 9) 전방 성적표 중복 제거: 같은 (종목,날짜) 두 버전 → 최신만
    rows = [
        ("005930", "2026-10-02", "v1.0", 0.60, "2026-10-02T09:00:00"),
        ("005930", "2026-10-02", "champ-20261002T024420-0.5513", 0.71, "2026-10-02T15:00:00"),
        ("000660", "2026-10-02", "champ-20261002T024420-0.5513", 0.40, "2026-10-02T15:00:00"),
    ]
    out = fs._dedupe_latest(rows)
    check("9 중복 제거 행수", len(out) == 2, str(len(out)))
    d = {(a, b): (c, e) for a, b, c, e in out}
    check("9 중복 제거 최신 채택", d[("005930", "2026-10-02")] ==
          ("champ-20261002T024420-0.5513", 0.71), str(d.get(("005930", "2026-10-02"))))

    # 10) 서로 다른 날짜는 모두 유지 + 정렬
    rows = [
        ("A", "2026-10-02", "v1", 0.1, "t1"),
        ("A", "2026-10-01", "v1", 0.2, "t1"),
    ]
    out = fs._dedupe_latest(rows)
    check("10 날짜별 유지·정렬", [r[1] for r in out] == ["2026-10-01", "2026-10-02"], str(out))

    print(f"\n=== {len(FAILS)} FAIL / 10 checks ===")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
