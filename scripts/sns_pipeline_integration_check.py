#!/usr/bin/env python3
"""SNS 피처의 **파이프라인 연결** 검증 — feature_pipeline.py 를 수정하지 않고
서브클래스로 동일한 패치 효과를 재현해 실측한다.

실행(stock_xgboost_ml 컨테이너, cwd=/app):
    python -u scripts/sns_pipeline_integration_check.py [--pairs 3]

검증
----
1. 이름 충돌: ``sns_feature_bundle.feature_names()`` 의 26개가 기존
   ``FeaturePipeline.get_feature_names()``(173개) 와 겹치지 않는가.
2. 패치 전: ``FeaturePipeline.build_features(stock, date)`` 에 SNS 키가 0개.
3. 패치 후(서브클래스로 재현): 동일 호출에서 SNS 키 26개가 들어오고 비영인가.
4. (종목,날짜) 3쌍에서 날짜별 변동.
"""
import argparse
import os
import sys

import psycopg2

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from app.feature_engine.feature_pipeline import FeaturePipeline       # noqa: E402
from app.feature_engine.sns_feature_bundle import (                   # noqa: E402
    SnsFeatureBundle, feature_names,
)


def connect():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ["POSTGRES_PASSWORD"],
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
    )


class PatchedPipeline(FeaturePipeline):
    """제안 패치(import + build_features 에 4줄)를 서브클래스로 그대로 재현."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.sns = SnsFeatureBundle(pg_conn=self.pg_conn)

    def build_features(self, stock_code, date=None, market_df=None):
        features = super().build_features(stock_code, date, market_df)
        # ↓↓↓ 제안 패치와 동일한 호출 (feature_pipeline.py 에 들어갈 4줄)
        try:
            features.update(self.sns.load(stock_code, date=date))
        except Exception as e:  # pragma: no cover - fail-open
            print(f"      (SNS 실패 {stock_code}: {e})")
        features["feature_count"] = len(features)
        return features


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", type=int, default=3)
    args = ap.parse_args()

    conn = connect()
    cur = conn.cursor()
    names = set(feature_names())

    print("=" * 100)
    base = FeaturePipeline(pg_conn=conn)
    existing = set(base.get_feature_names())
    print(f"[1] 기존 get_feature_names(): {len(existing)}개 / SNS 이름: {len(names)}개")
    clash = sorted(names & existing)
    print(f"    이름 충돌: {len(clash)}개 {clash if clash else '(없음 → 접두 불필요/안전)'}")
    print(f"    패치 후 합계: {len(existing | names)}개")

    cur.execute(
        """SELECT stock_code, trade_date FROM sns_post_features
           WHERE attention_score <> 0
           GROUP BY 1,2 ORDER BY max(post_count) DESC LIMIT %s""",
        (args.pairs,),
    )
    pairs = [(r[0], r[1]) for r in cur.fetchall()]

    print("=" * 100)
    print("[2] 패치 전 build_features — SNS 키 존재 여부")
    for code, d in pairs:
        f = base.build_features(code, str(d))
        hit = sorted(k for k in f if k in names)
        print(f"  {code} @ {d}: 전체 {len(f)-3}개 피처 / SNS 키 {len(hit)}개 {hit}")
        base.clear_cache()

    print("=" * 100)
    print("[3] 패치 후(서브클래스 재현) build_features — SNS 키 값")
    patched = PatchedPipeline(pg_conn=conn)
    sigs = {}
    for code, d in pairs:
        f = patched.build_features(code, str(d))
        hit = {k: round(float(f[k]), 4) for k in f if k in names}
        nz = {k: v for k, v in hit.items() if v != 0.0}
        print(f"  {code} @ {d}: 전체 {len(f)-3}개 / SNS 키 {len(hit)}/26 / 비영 {len(nz)}")
        for k in sorted(nz):
            print(f"      {k} = {nz[k]}")
        sigs[(code, str(d))] = tuple(sorted(nz.items()))
        patched.clear_cache()

    print("=" * 100)
    if len(sigs) > 1:
        uniq = len(set(sigs.values()))
        print(f"[4] (종목,날짜) {len(sigs)}쌍 중 서로 다른 SNS 벡터: {uniq}/{len(sigs)}"
              f"  → 값이 종목·날짜에 따라 달라진다")
    conn.close()


if __name__ == "__main__":
    main()
