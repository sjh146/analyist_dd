#!/usr/bin/env python3
"""select_training_universe 결정성 프로브 (읽기 전용 진단).

가설: `_fetch_eligible` 은 ORDER BY 가 없고, `eligible.sort(key=(latest is None, latest))` 는
`latest` 동률에서 SQL 반환 순서(=실행마다 달라질 수 있음)를 그대로 쓴다. 대부분의 종목이 같은
`latest`(최근 거래일)를 가지므로 `top = eligible[:limit*3]` 의 **집합 자체**가 실행마다 흔들릴 수 있다.

실측 동기(2026-10-01 03:0x CG33): 같은 커맨드 안 두 arm 이 5분 간격으로 돌았는데 표본 구성이
`교집합 22` vs `교집합 25` 로 달랐다 → '같은 창·같은 종목' 짝 비교가 성립하지 않았다.

판정: 같은 프로세스 내 반복 / 새 커넥션 / 새 프로세스 각각에서 유니버스가 동일해야 PASS.
"""
import os
import sys

import psycopg2

from app.training.universe import select_training_universe, _fetch_eligible, _default_date_from


def conn():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", 5432)),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD"),
    )


def probe(cur, limit, label):
    runs = [select_training_universe(cur, limit=limit, min_days=30, seed=0) for _ in range(4)]
    base = set(runs[0])
    inter = [len(base & set(r)) for r in runs]
    uniq = len({tuple(r) for r in runs})
    print(f"[{label}] limit={limit} 실행 {len(runs)}회 · run0 과의 교집합 {inter} · 서로 다른 결과 {uniq}개")
    if len(set(map(tuple, runs))) > 1:
        for i in range(1, len(runs)):
            d = base ^ set(runs[i])
            print(f"    run0 Δrun{i}: {sorted(d)[:12]}")
    return inter, uniq


def main():
    c = conn()
    fail = 0

    # 1) 같은 커넥션 반복
    inter, uniq = probe(c, 60, "same-conn")
    if uniq != 1:
        fail += 1
    inter200, uniq200 = probe(c, 200, "same-conn")
    if uniq200 != 1:
        fail += 1

    # 2) 새 커넥션
    uniqs = []
    runs_new = []
    for _ in range(3):
        c2 = conn()
        runs_new.append(select_training_universe(c2, limit=60, min_days=30, seed=0))
        c2.close()
    base = set(select_training_universe(c, limit=60, min_days=30, seed=0))
    inter_new = [len(base & set(r)) for r in runs_new]
    uniq_new = len({tuple(r) for r in runs_new})
    print(f"[new-conn] limit=60 새 커넥션 3회 · run 과의 교집합 {inter_new} · 서로 다른 결과 {uniq_new}개")
    if uniq_new != 1:
        fail += 1

    # 3) 원천(eligible) 행 순서 안정성 — 동률 그룹의 크기 확인
    e1 = _fetch_eligible(c, _default_date_from(), 30)
    e2 = _fetch_eligible(c, _default_date_from(), 30)
    same_order = [r["code"] for r in e1] == [r["code"] for r in e2]
    print(f"[eligible] 조회 {len(e1)}행 · 두 번 조회 순서 동일: {same_order}")
    if not same_order:
        fail += 1
    from collections import Counter
    cnt = Counter(r["latest"] for r in e1)
    top = cnt.most_common(3)
    print("[eligible] latest 동률 상위:", [(str(k), v) for k, v in top])
    # 최대 동률 그룹이 limit*3 컷을 덮는가
    biggest = top[0][1] if top else 0
    print(f"[eligible] 최대 동률 그룹 {biggest}종목 vs top 컷(limit*3=180/600) → "
          f"{'컷이 동률 그룹 내부를 자름(집합 비결정)' if biggest > 180 else '컷이 동률 그룹 밖'}")

    print(f"\n{'ALL PASS (결정적)' if not fail else str(fail) + ' FAIL (비결정 확인)'}")
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
