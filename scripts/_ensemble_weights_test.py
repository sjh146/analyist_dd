#!/usr/bin/env python3
"""자체점검: 앙상블 val-AUC 가중치 영속화 + 배포 동작 불변(2026-10-06).

배경(실측): 학습 경로(retrain_champion)는 ``meta.ensemble_auc``·``auc.txt`` 를 val-AUC **가중**
평균으로 보고하지만, ``EnsembleModel.load()`` 는 가중치를 복원하지 않아 배포 추론은 **균등**
평균이었다 = 승격 게이트가 비교하는 지표와 실제로 도는 점수가 다른 함수다.

이 테스트가 잠그는 계약:
  ① ``save()`` 는 ``ensemble_weights.json`` 을 남긴다(부산물).
  ② ENV ``ENSEMBLE_USE_STORED_WEIGHTS`` 가 **꺼져 있으면**(기본) 저장된 가중치가 있어도
     균등 평균 = 종전 배포 동작과 **비트 동일**하다.
  ③ 플래그를 켜면 저장된 가중치로 가중 평균한다(예측이 실제로 달라진다).
  ④ 파일 없음/깨짐/빈 dict → 예외 없이 균등 경로로 폴백.

무거운 학습 없이 스텁 서브모델로만 검사한다(수 초).
"""
import json
import os
import shutil
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models.ensemble_model import (  # noqa: E402
    EnsembleModel,
    ENV_USE_STORED_WEIGHTS,
    WEIGHTS_FILENAME,
)

PASS = 0
FAIL = 0


def check(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS: {msg}")
    else:
        FAIL += 1
        print(f"  FAIL: {msg}")


class _StubSub:
    """확률 상수 벡터를 돌려주는 서브모델 스텁(가중/균등 차이를 해석적으로 계산 가능)."""

    def __init__(self, name, base):
        self.name = name
        self.base = base
        self.path = None

    def predict(self, X):
        return np.full(len(X), float(self.base))

    def predict_single(self, features):
        return {"predicted_probability": float(self.base),
                "direction": "up" if self.base >= 0.5 else "down"}

    def save(self, p):
        self.path = p

    def load(self, p):
        self.path = p
        return True


def _make(tmp, weights=None, weights_file=True, payload=None):
    """가중치를 가진 앙상블(스텁) 생성. weights_file=False 면 파일을 지운다."""
    ens = EnsembleModel(model_dir=tmp)
    bases = {"xgboost": 0.60, "lightgbm": 0.55, "catboost": 0.40}
    ens.model_names = ["xgboost", "lightgbm", "catboost"]
    ens.models = [_StubSub(n, bases[n]) for n in ens.model_names]
    ens.val_weights = dict(weights) if weights else {}
    if weights_file:
        fp = os.path.join(tmp, WEIGHTS_FILENAME)
        with open(fp, "w") as f:
            json.dump(payload if payload is not None else ens.val_weights, f, indent=2)
    return ens, bases


def main():
    tmp = tempfile.mkdtemp(prefix="ens_w_test_")
    old = os.environ.pop(ENV_USE_STORED_WEIGHTS, None)
    try:
        W = {"xgboost": 0.0492, "lightgbm": 0.0448, "catboost": 0.0185}

        # ── ① save() 가 가중치 파일을 남긴다 ─────────────────────────────────
        print("[1] save() 부산물")
        ens, _ = _make(tmp, weights=W, weights_file=False)
        paths = ens.save(tmp)
        check(len(paths) == 3, "save() 가 서브모델 3개 경로를 돌려준다")
        fp = os.path.join(tmp, WEIGHTS_FILENAME)
        check(os.path.exists(fp), f"save() 가 {WEIGHTS_FILENAME} 를 남긴다")
        check(json.load(open(fp)) == W, "저장 내용이 val_weights 와 일치한다")

        print("[1b] save_weights: 가중치 없으면 아무것도 쓰지 않는다")
        tmp_b = tempfile.mkdtemp(prefix="ens_w_test_b_")
        ens_b, _ = _make(tmp_b, weights={}, weights_file=False)
        check(ens_b.save_weights(tmp_b) is None, "빈 val_weights → None(파일 미생성)")
        check(not os.path.exists(os.path.join(tmp_b, WEIGHTS_FILENAME)), "파일이 생기지 않았다")
        shutil.rmtree(tmp_b, ignore_errors=True)

        # ── ② 플래그 OFF = 균등 평균(배포 동작 불변) ──────────────────────────
        print("[2] 플래그 OFF → 균등 평균 (종전 배포 동작)")
        ens, bases = _make(tmp, weights=W, weights_file=True)
        ens.val_weights = {}  # load() 가 채우지 않는 상태에서 시작
        got = ens.load_weights(tmp)
        check(got == {}, "load_weights() 가 빈 dict(=미채택)를 돌려준다")
        check(ens.val_weights == {}, "val_weights 가 비어 있다(=weight 1.0 경로)")
        X = np.zeros((4, 2))
        equal = float(np.mean([bases[n] for n in ens.model_names]))
        check(abs(float(ens.predict(X)[0]) - equal) < 1e-12, f"predict == 균등 평균({equal:.6f})")
        single = ens.predict_single(np.zeros(2))["ensemble"]["probability"]
        check(abs(single - equal) < 1e-12, "predict_single == 균등 평균")

        # ── ③ 플래그 ON = 가중 평균(실제로 달라진다) ─────────────────────────
        print("[3] 플래그 ON → 가중 평균")
        os.environ[ENV_USE_STORED_WEIGHTS] = "1"
        ens, bases = _make(tmp, weights=W, weights_file=True)
        ens.val_weights = {}
        got = ens.load_weights(tmp)
        check(got == W, "load_weights() 가 파일 내용을 채택한다")
        num = sum(W[n] * bases[n] for n in ens.model_names)
        den = sum(W.values())
        weighted = num / den
        check(abs(float(ens.predict(X)[0]) - weighted) < 1e-12, f"predict == 가중 평균({weighted:.6f})")
        check(abs(weighted - equal) > 1e-6, "가중 평균 != 균등 평균(플래그가 실제로 동작한다)")
        single = ens.predict_single(np.zeros(2))["ensemble"]["probability"]
        check(abs(single - weighted) < 1e-12, "predict_single == 가중 평균")
        del os.environ[ENV_USE_STORED_WEIGHTS]

        # ── ④ 파일 없음 / 깨짐 / 빈 dict → 균등 폴백 ─────────────────────────
        print("[4] 폴백 경로(예외 없이 균등)")
        os.environ[ENV_USE_STORED_WEIGHTS] = "1"
        ens, bases = _make(tmp, weights=W, weights_file=False)
        os.remove(os.path.join(tmp, WEIGHTS_FILENAME))
        ens.val_weights = {}
        check(ens.load_weights(tmp) == {}, "파일 없음 → 빈 dict")
        check(abs(float(ens.predict(X)[0]) - equal) < 1e-12, "파일 없음 → 균등 평균")

        ens, bases = _make(tmp, weights=W, weights_file=True, payload=None)
        with open(os.path.join(tmp, WEIGHTS_FILENAME), "w") as f:
            f.write("{ this is not json")
        ens.val_weights = {}
        check(ens.load_weights(tmp) == {}, "깨진 JSON → 빈 dict")
        check(abs(float(ens.predict(X)[0]) - equal) < 1e-12, "깨진 JSON → 균등 평균")

        ens, bases = _make(tmp, weights=W, weights_file=True, payload={})
        ens.val_weights = {}
        check(ens.load_weights(tmp) == {}, "빈 dict payload → 빈 dict")
        del os.environ[ENV_USE_STORED_WEIGHTS]

        # ── ⑤ env 값 해석 ────────────────────────────────────────────────────
        print("[5] env 플래그 문자열 해석")
        for v, want in (("1", True), ("true", True), ("YES", True), ("on", True),
                        ("", False), ("0", False), ("no", False), ("off", False)):
            os.environ[ENV_USE_STORED_WEIGHTS] = v
            ens, _ = _make(tmp, weights=W, weights_file=True)
            ens.val_weights = {}
            adopted = ens.load_weights(tmp) != {}
            check(adopted == want, f"값 {v!r} → 채택={adopted} (기대 {want})")
        os.environ.pop(ENV_USE_STORED_WEIGHTS, None)

    finally:
        if old is not None:
            os.environ[ENV_USE_STORED_WEIGHTS] = old
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\nPASS {PASS} · FAIL {FAIL}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
