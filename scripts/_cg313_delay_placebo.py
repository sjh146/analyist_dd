#!/usr/bin/env python3
"""[CG313] 실행 지연 플레이스보 — 예측일을 '한 거래일 뒤'로 옮긴 arm 파일을 만든다.

WHY(2026-11-17): CG311 의 q0.05 top-k 순기대(+6.069%p/세션, t 3.83)는 **진입가 = 신호일 t 의
종가**를 가정한다. 그 종가는 t 시점 종가를 포함한 피처로 예측한 값이라, 실제 주문(다음 거래일
개장/종가)에서는 하루 늦게 산다. 그 하루가 신호의 원천이면 축은 배포 불가다.

방법: 대조군 예측 파일의 (fold, date) 목록으로 그 폴드 안의 '다음 거래일' 매핑을 만들어
arm 행의 date 를 한 칸 미룬다. 폴드 경계를 넘지 않는다(넘으면 대조군 풀이 없는 날짜가 된다).
점수·종목은 그대로 두므로 '어제의 랭킹으로 오늘 산다'는 순수 지연 효과만 남는다.

출력: --out jsonl (arm 형식 동일). 이후 scripts/fillable_topk_expectancy.py 로 채점한다.
"""
import argparse
import json
from collections import defaultdict


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm-jsonl", required=True)
    ap.add_argument("--calendar-jsonl", required=True,
                    help="(fold,date) 달력의 정본 — 보통 대조군 예측 파일")
    ap.add_argument("--out", required=True)
    ap.add_argument("--lag", type=int, default=1, help="미룰 거래일 수(기본 1)")
    ap.add_argument("--drop-last", action="store_true", default=True,
                    help="폴드 끝에서 밀려 매핑이 없는 행은 버린다(기본)")
    a = ap.parse_args()

    per_fold = defaultdict(set)
    with open(a.calendar_jsonl) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            per_fold[r["fold"]].add(r["date"])

    nxt = {}
    for fold, dates in per_fold.items():
        ds = sorted(dates)
        for i, d in enumerate(ds):
            j = i + a.lag
            if j < len(ds):
                nxt[(fold, d)] = ds[j]

    kept = dropped = 0
    with open(a.arm_jsonl) as f, open(a.out, "w") as g:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            key = (r["fold"], r["date"])
            d2 = nxt.get(key)
            if d2 is None:
                dropped += 1
                continue
            r["date"] = d2
            r["date_signal"] = key[1]
            g.write(json.dumps(r, ensure_ascii=False) + "\n")
            kept += 1

    print(f"[cg313] lag={a.lag} kept={kept} dropped={dropped} out={a.out}")


if __name__ == "__main__":
    main()
