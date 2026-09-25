"""ml 드라이버가 실제로 쓰는 CORE_FEATURES 는 어느 파일의 것인가?

발단: wf_label_sweep 이 넘긴 선별 30개 중 이름 기준 core 교집합은 10~12개인데,
train_seed 가 돌려준 curated 길이는 30이었다 → 두 수치가 모순.
가설: ml(=extra_experiments._load_driver())이 import 하는 train_curated 가
/app/scripts/train_curated.py 가 **아닌 다른 파일**이고, 그 CORE 목록이 다르다.
"""
import sys

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/scripts")

import extra_experiments as ex  # noqa: E402

ml = ex._load_driver()
print("ml module file:", getattr(ml, "__file__", None))
print("ml name:", getattr(ml, "__name__", None))
for attr in ("tc", "train_curated"):
    mod = getattr(ml, attr, None)
    if mod is not None:
        print(f"ml.{attr} file:", getattr(mod, "__file__", None),
              "| CORE:", len(getattr(mod, "CORE_FEATURES", []) or []))
print("ml sys.path[0:4]:", sys.path[:4])

# ml 이 실제로 select_curated_features 에서 쓰는 함수의 정의 위치
f = getattr(ml, "tc", None)
if f is not None and hasattr(f, "select_curated_features"):
    print("select_curated_features:", f.select_curated_features.__module__,
          f.select_curated_features.__code__.co_filename,
          f.select_curated_features.__code__.co_firstlineno)
    core = list(f.CORE_FEATURES)
    print("core len", len(core), "head:", core[:20])

# 두 파일 비교
import train_curated as tc_direct  # noqa: E402
print("\ndirect import train_curated:", tc_direct.__file__,
      "CORE:", len(tc_direct.CORE_FEATURES), "head:", list(tc_direct.CORE_FEATURES)[:20])

# 이름 기준 대조
probe = ["atr", "bayes_momentum_1d", "quality_roa", "quality_roe", "ma_position_20", "return_5d"]
print("\nprobe membership:")
for p in probe:
    print(f"  {p:22s} direct={p in tc_direct.CORE_FEATURES} driver={p in f.CORE_FEATURES}" if f else "")
