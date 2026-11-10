#!/usr/bin/env python3
"""_meta_range 의 fit_end 우선 규칙 회귀 테스트 (2026-11-10 CG183).

왜: 재학습 후보처럼 데이터 창 끝까지 학습한 모델은 data_end 를 학습 종료로 읽으면
champion_robust_eval._split_oos 가 **모든 평가창을 겹침으로 버려** OOS 를 잴 수 없다.
retrain_champion 이 fit_end(마지막 적합일)·val_start(검증 첫날)를 기록하고, 평가 경로는
fit_end 를 우선하되 **옛 모델(fit_end 없음)은 종전대로 data_end 로 폴백**해야 한다.

실행: python3 scripts/_meta_range_fitend_test.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import champion_robust_eval as cre  # noqa: E402

fails = []


def check(name, got, want):
    ok = got == want
    print(("PASS " if ok else "FAIL ") + f"{name}: got={got!r} want={want!r}")
    if not ok:
        fails.append(name)


def mkdir_with_meta(meta):
    d = tempfile.mkdtemp(prefix="metarange_")
    with open(os.path.join(d, "training-result-20260101-000000.json"), "w") as f:
        json.dump(meta, f)
    return d


d1 = mkdir_with_meta({"data_start": "2026-07-10", "data_end": "2026-11-09",
                      "fit_end": "2026-10-15", "val_start": "2026-10-19"})
check("fit_end 우선(창 끝 11-09 은 무시)", cre._meta_range(d1),
      ("2026-07-10", "2026-10-15"))

d2 = mkdir_with_meta({"data_start": "2026-07-03", "data_end": "2026-10-01"})
check("옛 모델 폴백(data_end)", cre._meta_range(d2), ("2026-07-03", "2026-10-01"))

d3 = mkdir_with_meta({"start_date": "2026-05-20", "end_date": "2026-09-23"})
check("구키 폴백(start_date/end_date)", cre._meta_range(d3), ("2026-05-20", "2026-09-23"))

d4 = tempfile.mkdtemp(prefix="metarange_empty_")
check("meta 없음 → (None,None)", cre._meta_range(d4), (None, None))

# _split_oos: fit_end 이후 창만 OOS 로 남는지(겹침 제거 실증)
windows = [["2026-10-02", "2026-10-12"], ["2026-10-13", "2026-10-16"],
           ["2026-10-20", "2026-10-26"], ["2026-10-27", "2026-11-02"]]
kept, excl = cre._split_oos(windows, "2026-07-10", "2026-10-15")
check("fit_end 기준 OOS 창", kept, [["2026-10-20", "2026-10-26"],
                                   ["2026-10-27", "2026-11-02"]])
check("학습겹침 제외 수", len(excl), 2)

print("\n결과:", "ALL PASS" if not fails else f"{len(fails)} FAIL {fails}")
sys.exit(0 if not fails else 1)
