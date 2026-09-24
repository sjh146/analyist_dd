#!/usr/bin/env python3
"""제안 패치를 **실제 파일에 적용한 사본**으로 엔드투엔드 검증한다.

``feature_pipeline.py`` 자체는 건드리지 않는다. 호스트에서

    cp /tmp/fp_patched.py services/xgboost-ml/app/feature_engine/_sns_patch_check.py

로 만든 사본(패치 적용본)을 import 해서 ``build_features`` 를 실제로 돌리고,
패치 전 ``FeaturePipeline`` 과 결과를 비교한다. 끝나면 사본은 삭제한다.

실행(stock_xgboost_ml 컨테이너, cwd=/app):
    python -u scripts/sns_patch_apply_check.py --stocks-dates 034020:2026-09-23,303810:2026-09-23
"""
import argparse
import importlib
import os
import sys

import psycopg2

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

PATCHED_MODULE = "app.feature_engine._sns_patch_check"


def connect():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ["POSTGRES_PASSWORD"],
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stocks-dates", type=str,
                    default="034020:2026-09-23,303810:2026-09-23,005930:2026-09-23")
    args = ap.parse_args()

    mod = importlib.import_module(PATCHED_MODULE)
    Patched = getattr(mod, "FeaturePipeline")
    from app.feature_engine.feature_pipeline import FeaturePipeline
    from app.feature_engine.sns_feature_bundle import feature_names

    names = set(feature_names())
    conn = connect()

    base = FeaturePipeline(pg_conn=conn)
    patched = Patched(pg_conn=conn)
    print("=" * 100)
    print(f"[1] 패치 사본 파일: {mod.__file__}")
    bn = set(base.get_feature_names())
    pn = set(patched.get_feature_names())
    print(f"    get_feature_names(): 패치 전 {len(bn)}개 → 패치 후 {len(pn)}개 "
          f"(추가 {len(pn - bn)}개, SNS 이름 누락 {sorted(names - pn) or '없음'})")
    print(f"    SNS 이름 26개가 모두 포함: {names <= pn}")

    print("=" * 100)
    print("[2] build_features 비교 (같은 DB, 같은 종목·날짜)")
    for token in args.stocks_dates.split(","):
        code, d = token.split(":")
        f0 = base.build_features(code, d)
        base.clear_cache()
        f1 = patched.build_features(code, d)
        s0 = {k for k in f0 if k in names}
        s1 = {k: float(f1[k]) for k in f1 if k in names}
        nz = {k: round(v, 4) for k, v in s1.items() if v != 0.0}
        print(f"  {code} @ {d}:")
        print(f"    패치 전: 전체 {len(f0)-3}개 피처, SNS 키 {len(s0)}개")
        print(f"    패치 후: 전체 {len(f1)-3}개 피처, SNS 키 {len(s1)}/26, 비영 {len(nz)}")
        for k in sorted(nz):
            print(f"        {k} = {nz[k]}")

    print("=" * 100)
    print("[3] 예외 없이 완료 — 패치 적용 가능")
    conn.close()


if __name__ == "__main__":
    main()
