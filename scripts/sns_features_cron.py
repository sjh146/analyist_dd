#!/usr/bin/env python3
"""sns_post_features 일일 갱신 크론 러너 (2026-11-20 배선 — 누락 스케줄 복구).

WHY: writer(`scripts/sns_features_writer.py`, 컨테이너 안에서 실행)는 2026-11-13 에 만들어졌지만
**스케줄이 없어** 11-13 12:06 이후 5거래일(11-16~11-20) 동안 sns_post_features 가 멈춰 있었다.
리더(feature_engine/sns_features.py)는 이 표를 읽으므로 SNS 감성·관심도 피처가 조용히 낡는다
(원천 sns_posts 는 11-20 09:05 까지 신선 — '수집은 도는데 가공이 멈춘' 유형이라 수집 신호에 안 걸린다).

자기신고(스킬 §러너 자기신고): source_rows = sns_posts 원시 게시글 수, claimed_rows = writer 가
만든 (종목,일) 행수. 둘은 단위가 다르다(집계 변환) — gap 해석 시 주의. persisted 는 테이블 델타.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))
import dq_claim  # noqa: E402

RUNNER = "sns_features_writer"
TABLE = "sns_post_features"
CONTAINER = "stock_xgboost_ml"
LOOKBACK = os.environ.get("SNS_FEATURE_LOOKBACK_DAYS", "9")


def main() -> int:
    dq_claim.claim_start(RUNNER, TABLE, note="lookback=%sd" % LOOKBACK)
    cmd = ["docker", "exec", "-w", "/app", CONTAINER, "python", "-u",
           "scripts/sns_features_writer.py", "--lookback-days", LOOKBACK]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    except subprocess.TimeoutExpired:
        print("[sns_features_cron] 타임아웃 1800s — writer 미완료", file=sys.stderr)
        dq_claim.claim_finish(RUNNER, claimed_rows=0, source_rows=None, note="timeout")
        return 2
    out = (p.stdout or "") + (p.stderr or "")
    print(out.rstrip())
    posts = re.search(r"게시글\s+(\d+)건", out)
    made = re.search(r"피처 행\s+(\d+)개 생성", out)
    n_posts = int(posts.group(1)) if posts else None
    n_made = int(made.group(1)) if made else 0
    dq_claim.claim_finish(RUNNER, claimed_rows=n_made, source_rows=n_posts,
                          note="posts->(stock,day) aggregate")
    print(json.dumps({"rc": p.returncode, "posts": n_posts, "feature_rows": n_made},
                     ensure_ascii=False))
    return p.returncode


if __name__ == "__main__":
    sys.exit(main())
