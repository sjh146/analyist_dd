#!/usr/bin/env python3
"""_factor_paired_test.py — CG117 짝 경로(모델 IC vs 팩터 IC) 자체점검.

검사 대상(합성 데이터 — 정답을 아는 값):
  ① `_sign_test_p` 부호검정 정확 p (동점 제외 규약)
  ② `paired_ic_stats` 의 ΔIC·t·동점 계수·공통세션 부족 처리
  ③ `add_factor_scores` 가 팩터 정의의 단일 진실원인지(횡단면 z·멀티팩터 합성)
  ④ `load_dump_rows` 가 두 키 표기(code/stock_code)와 결측 행을 올바로 거르는지

실행: `docker exec -e PYTHONPATH=/app stock_xgboost_ml python /app/scripts/_factor_paired_test.py`
(pandas/numpy 필요 — 호스트에 없으면 NOTE 만 남기고 0 을 돌려준다. 진짜 검증은 컨테이너에서.)
"""
import json
import os
import sys
import tempfile

FAIL = []
N_OK = 0


def check(name, cond, info=None):
    global N_OK
    if cond:
        N_OK += 1
        print(f"  [PASS] {name} {info}")
    else:
        FAIL.append(name)
        print(f"  [FAIL] {name} {info}")


def main():
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
    try:
        import pandas as pd                                        # noqa: F401
        import factor_money_screen as fm
    except ImportError as e:
        print(f"NOTE: pandas/numpy 없음({e}) — 컨테이너에서 실행하라(호스트는 검증 불가)")
        return 0

    # ① 부호검정
    check("sign_p(5,5)=0.0625", fm._sign_test_p(5, 5) == 0.0625, fm._sign_test_p(5, 5))
    check("sign_p(9,10)=0.0215", fm._sign_test_p(9, 10) == 0.0215, fm._sign_test_p(9, 10))
    check("sign_p(5,10) 상한 1.0", fm._sign_test_p(5, 10) == 1.0, fm._sign_test_p(5, 10))
    check("sign_p(n=0) → None", fm._sign_test_p(0, 0) is None, fm._sign_test_p(0, 0))

    # ② paired_ic_stats
    #  (a) 모델이 일관되게 우위 (Δ 분산有) → Δ>0, t>2, 동점 0
    mic = {f"d{i}": 0.10 + 0.001 * (i % 3) for i in range(10)}
    fic = {f"d{i}": 0.02 + 0.001 * (i % 3) for i in range(10)}
    st = fm.paired_ic_stats(mic, fic)
    check("(a) ΔIC 평균 +0.08", abs(st["mean_delta_ic"] - 0.08) < 1e-9, st["mean_delta_ic"])
    check("(a) 동점 0 · n 10", st["n_tied"] == 0 and st["n_sessions"] == 10, st)
    check("(a) 양(+)세션 1.0 · 부호검정 p 유의", st["pos_session_share"] == 1.0
          and st["sign_p"] is not None and st["sign_p"] < 0.05, st["sign_p"])
    check("(a) 극소 분산(fp 잔차) → t None (1e16 폭주 금지)",
          st["sd"] < 1e-9 and st["t"] is None, (st["sd"], st["t"]))
    #  (b) 완전 동일 → Δ 0, 전부 동점(부호검정 불가)
    stb = fm.paired_ic_stats(mic, dict(mic))
    check("(b) Δ0 · 전부 동점 · sign_p None",
          stb["n_tied"] == 10 and stb["mean_delta_ic"] == 0.0 and stb["sign_p"] is None, stb)
    check("(b) sd 0 → t None(제로분산은 유의성 없음 — rank_ic_money 와 같은 규약)",
          stb["sd"] == 0.0 and stb["t"] is None, (stb["sd"], stb["t"]))
    #  (c) 팩터가 우위 → Δ<0
    stc = fm.paired_ic_stats(fic, mic)
    check("(c) 팩터 우위 → ΔIC −0.08", abs(stc["mean_delta_ic"] + 0.08) < 1e-9, stc["mean_delta_ic"])
    #  (d) 부분 겹침 → 공통 세션만
    std = fm.paired_ic_stats({"a": 0.1, "b": 0.2, "z": 0.3}, {"a": 0.05, "b": 0.06})
    check("(d) 공통 2세션만 사용", std["n_sessions"] == 2
          and abs(std["mean_delta_ic"] - (0.05 + 0.14) / 2) < 1e-9, std)
    #  (e) 공통 1세션 → 집계 불가
    ste = fm.paired_ic_stats({"a": 0.1}, {"a": 0.05})
    check("(e) 공통 1세션 → error", bool(ste.get("error")) and ste["n_sessions"] == 1, ste)
    #  (f) 실제 분산이 있으면 t 유한 (가드가 정상 케이스를 막지 않는다)
    stf = fm.paired_ic_stats({"a": 0.10, "b": 0.20, "c": 0.30, "d": 0.40},
                             {"a": 0.05, "b": 0.14, "c": 0.30, "d": 0.38})
    check("(f) 분산 있으면 t 유한(가드 오작동 없음)", stf["t"] is not None and abs(stf["t"]) < 100,
          (stf["sd"], stf["t"], stf["mean_delta_ic"]))

    # ③ add_factor_scores — 횡단면 z + 멀티팩터 (성분 전부 변동 → 결측 전파 없음)
    df = pd.DataFrame({
        "code": [f"{i:06d}" for i in range(40)] * 2,
        "date": ["2026-01-02"] * 40 + ["2026-01-05"] * 40,
        "value_per": list(range(40)) * 2,
        "value_pbr": list(range(40, 80)) * 2, "value_psr": [i * 2.0 for i in range(40)] * 2,
        "quality_roe": list(range(40)) * 2, "quality_roa": [i * 3.0 for i in range(40)] * 2,
        "quality_f_score": [i * 0.5 for i in range(40)] * 2,
        "mom_60_5": list(range(40)) * 2, "lowvol_raw": list(range(40)) * 2,
    })
    df2, parts = fm.add_factor_scores(df)
    check("멀티팩터 컬럼 생성", "multifactor" in df2.columns and len(parts) == 4, parts)
    check("횡단면 z 평균≈0(날짜별 표준화)",
          abs(df2["z_value_per"].mean()) < 1e-9, df2["z_value_per"].mean())
    check("부호 사전 고정(가치=낮은 배수) → value_score 가 value_per 와 역방향",
          df2["value_score"].corr(df2["z_value_per"]) < 0,
          df2["value_score"].corr(df2["z_value_per"]))
    check("quality 는 ROE 와 같은 방향(+)",
          df2["quality_score"].corr(df2["z_quality_roe"]) > 0,
          df2["quality_score"].corr(df2["z_quality_roe"]))
    check("multifactor = 4성분 평균", abs(df2["multifactor"].iloc[0]
          - df2[parts].iloc[0].mean()) < 1e-9, df2["multifactor"].iloc[0])
    # 결측 전파(문서화된 의미): 성분 z 가 결측이면 합성 점수 '전체'가 결측이 된다
    # (행 단위 조인 가정 — 재무 6컬럼은 함께 있거나 함께 없다. 커버리지 0.592 실측과 정합)
    dfc = df.copy()
    dfc["value_pbr"] = 1.0                     # 상수 → z 결측
    dfc2, _ = fm.add_factor_scores(dfc)
    check("성분 z 결측이면 합성 점수가 전파 결측(nan)", int(dfc2["value_score"].notna().sum()) == 0,
          int(dfc2["value_score"].notna().sum()))
    check("다른 성분(momentum)은 살아 있다", int(dfc2["momentum_score"].notna().sum()) == 80,
          int(dfc2["momentum_score"].notna().sum()))

    # ③b 정의 단일 진실원 — 인라인 combo 복제가 없어야 한다(정의 이탈 방지)
    src = open(fm.__file__, encoding="utf-8").read()
    check("팩터 합성 코드는 add_factor_scores 한 곳에만", src.count("def combo(") == 1,
          src.count("def combo("))
    check("main 이 add_factor_scores 를 호출", "df, parts = add_factor_scores(df)" in src)

    # ④ load_dump_rows
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as fh:
        fh.write(json.dumps({"code": "000001", "date": "2026-01-02", "y_pred": 0.5,
                             "fwd_ret": 0.01}) + "\n")
        fh.write(json.dumps({"stock_code": "000002", "prediction_date": "2026-01-02",
                             "y_pred": 0.4, "fwd_ret": -0.02}) + "\n")
        fh.write(json.dumps({"code": "000003", "date": "2026-01-02", "y_pred": None,
                             "fwd_ret": 0.0}) + "\n")          # y_pred 결측 → 제외
        fh.write("{ 깨진 줄\n")                                # 파싱 불가 → 제외
        path = fh.name
    try:
        d = fm.load_dump_rows(path)
    finally:
        os.remove(path)
    check("덤프 파싱: 유효 2행 · 두 키 표기 모두 인식", len(d) == 2, len(d))
    check("덤프 파싱: 코드·날짜 형식", set(d["code"]) == {"000001", "000002"}
          and set(d["date"]) == {"2026-01-02"}, d.to_dict("records"))

    print(f"\n{'ALL PASS' if not FAIL else 'FAILURES: ' + ', '.join(FAIL)}  ({N_OK} PASS)")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
