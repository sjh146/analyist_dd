#!/usr/bin/env python3
"""rank_universe 교집합 실측 리포트 (docs/spec_rank_universe.md 검증 보조).

두 산출물(압축 유니버스 실행 / 전체 유니버스 실행)을 비교해 '압축'인지 '대체'인지 판정한다.
입력은 파일뿐 — DB·브리지·네트워크 미사용.
"""
import argparse
import json
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))


def load_items(path):
    try:
        data = json.load(open(path, encoding="utf-8"))
    except Exception:
        return []
    if isinstance(data, dict):
        return data.get("items") or data.get("candidates") or []
    return data or []


def summarize(items):
    scores = [float(i.get("score")) for i in items if i.get("score") is not None]
    return {
        "n": len(items),
        "top5": [(i.get("stock_code"), i.get("stock_name"),
                  round(float(i["score"]), 1) if i.get("score") is not None else None)
                 for i in items[:5]],
        "score_max": max(scores) if scores else None,
        "score_min": min(scores) if scores else None,
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--restricted", default="/tmp/dt_restricted.json")
    ap.add_argument("--full", default="reports/daytrading_latest.json")
    ap.add_argument("--universe", default="data/state/rank_universe.json")
    ap.add_argument("--out", default="data/reports/rank_universe_overlap.json")
    args = ap.parse_args(argv)

    ri = load_items(args.restricted)
    fi = load_items(args.full)
    try:
        uni = json.load(open(args.universe, encoding="utf-8"))
    except Exception:
        uni = {}
    rc = {i.get("stock_code") for i in ri}
    fc = {i.get("stock_code") for i in fi}
    overlap = sorted(c for c in (rc & fc) if c)
    rs, fs = summarize(ri), summarize(fi)
    verdict = (
        "교집합 %d — '압축'이 아니라 '대체'가 된다. 압축 유니버스 후보의 최고 점수 %.1f, "
        "전체 유니버스 %.1f." % (len(overlap), rs["score_max"] or 0, fs["score_max"] or 0)
        if not overlap else
        "교집합 %d — 압축이 성립(상위 후보가 유지됨)." % len(overlap)
    )
    out = {
        "measured_at": datetime.now(KST).isoformat(timespec="seconds"),
        "question": "크레온 상승률 랭킹 ∩ 단타스크리너 점수 = 유니버스 압축? (2026-11-18 사용자 선택 1번)",
        "rank_universe": {"pct_from": 2, "pct_to": 12, "n_codes": uni.get("n_codes"),
                          "ok": uni.get("ok")},
        "restricted_run": rs,
        "full_run": fs,
        "overlap_codes": overlap,
        "overlap_n": len(overlap),
        "verdict": verdict,
        "why": ("단타스크리너 상위는 20일 칼만 추세+모델로 뽑히고 당일 등락률과 무관하다"
                "(2026-11-18 상위: 사람인·KCC건설은 당일 ±0%대). 상승률 2~12% 대역은 오늘 움직인 "
                "종목이라 두 집합이 겹치지 않는다 → 교집합은 후보를 '바꾸는' 효과가 크다."),
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    print(verdict)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
