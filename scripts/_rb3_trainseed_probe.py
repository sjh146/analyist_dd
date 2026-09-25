"""train_seed 반환값 검증: len(_c) 가 정말 'curated 게이트 통과 수' 인가?

스모크 실측에서 계측값 n_effective == len(sel) (30, 48) 로 나왔는데,
이름 기준 core 교집합은 10~12 였다 → 계측 코드가 잘못 읽은 값을 보고 있다는 뜻.
여기서 실제 반환값을 직접 찍어 확정한다.
"""
import sys

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/scripts")

import numpy as np  # noqa: E402

import extra_experiments as ex  # noqa: E402
import train_curated as tc  # noqa: E402

ml = ex._load_driver()

SEL = ["atr", "atr_pct", "bayes_momentum_1d", "bayes_volatility", "beta_60d",
       "quality_asset_growth", "quality_beta", "quality_cp_to_assets", "quality_f_score",
       "quality_op_to_equity", "quality_roa", "quality_roe", "ma_position_20", "return_5d",
       "return_20d", "volatility_20d", "volume_ratio_5", "price", "net_income", "revenue",
       "net_margin", "op_margin", "operating_profit", "ma_position_5", "ma_position_60",
       "ma_position_120", "volume_ratio_20", "momentum_vs_volatility", "trend_interaction",
       "similar_stocks_return_avg"]

core = set(tc.CORE_FEATURES)
in_core = [f for f in SEL if f in core]
print(f"입력 {len(SEL)}개 · 이름 기준 core 교집합 {len(in_core)}개")
print("core 교집합:", in_core)
print("select_curated_features 직접 호출 결과:", len(tc.select_curated_features(SEL, True)),
      tc.select_curated_features(SEL, True)[:8])

rng = np.random.default_rng(0)
n = 400
X = rng.normal(size=(n, len(SEL))).astype(np.float32)
y = (rng.random(n) > 0.5).astype(int)
a, m, c, e = ml.train_seed(X, None, X, y, None, y, list(SEL),
                           "/tmp/_rb3_probe_ens", 0, 0.03, 4, 1500, True, None)
print("\ntrain_seed 반환: auc=", a)
print("  model_aucs keys:", list(m)[:5], "n=", len(m))
print("  curated 반환 타입:", type(c).__name__, "len=", len(c) if hasattr(c, "__len__") else "?")
print("  curated 내용(앞 8):", list(c)[:8] if hasattr(c, "__iter__") else c)
print("  ensemble.model_names:", getattr(e, "model_names", None))
