#!/usr/bin/env python3
"""MT49 자체점검 — 검증 성적표(champion_scorecard)와 원장 `validation` 블록 계약.

무엇을 보증하나(트레이더 환류 need-fold-scorecard, 2026-10-05):
  ① 성적표 산출물이 폴드 통계(mean±std·fold_win_rate)·purge·승격 dry-run 을 담는다
  ② 구동기 append_ledger 가 **모든 원장 기록**에 그 요약을 `validation` 블록으로 싣는다
  ③ 트레이더 `verify_model_handoff` 의 키워드 검사(folds·std·fold_win_rate·purge·dry-run)가
     최근 5행에서 전부 통과한다 — 즉 계약 2번 gaps 가 비워진다
  ④ 성적표가 없거나 손상돼도 기록이 죽지 않는다(블록 생략)

실행(호스트 python3 — 도커 불필요):
    python3 scripts/_scorecard_contract_test.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import champion_scorecard as cs                     # noqa: E402
import model_engineer_cycle as me                   # noqa: E402

FAILS: list[str] = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f" — {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


def trader_keyword_flags(rows):
    """trader_cycle.verify_model_handoff 의 검사 규칙을 그대로 재현한다(문자열 기반)."""
    blob = json.dumps(rows[-5:], ensure_ascii=False) if rows else ""
    has = {"folds": any(k in blob for k in ("folds", "fold", "fold_auc")),
           "mean_std": ("std" in blob or "±" in blob),
           "fold_win_rate": ("fold_win" in blob or "폴드 승률" in blob or "win_rate" in blob),
           "purge": "purge" in blob,
           "promote_dryrun": any(k in blob for k in ("dry-run", "dry_run", "dryrun"))}
    return has, [k for k, v in has.items() if not v]


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="mt49_")

    # ① 실제 성적표(없으면 생성)
    if not os.path.exists(cs.DEFAULT_OUT):
        cs.main() if False else None
        rep = cs.build(cs.DEFAULT_MODEL_DIR, cs.DEFAULT_LEDGER)
        with open(cs.DEFAULT_OUT, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=2)
    sc = json.load(open(cs.DEFAULT_OUT, encoding="utf-8"))
    fs = sc.get("fold_stats") or {}
    check("① 성적표에 폴드 통계", all(fs.get(k) is not None for k in
                                   ("folds", "mean", "std", "fold_win_rate", "n_folds")), str(fs.get("folds")))
    check("① 성적표에 purge 정책", bool((sc.get("purge") or {}).get("policy")))
    check("① 성적표에 승격 dry-run", bool((sc.get("promote_dryrun") or {}).get("status")),
          str((sc.get("promote_dryrun") or {}).get("status")))
    check("① 성적표에 출처(measured_at)", bool(fs.get("measured_at")), str(fs.get("measured_at")))

    # ② 블록 주입
    vb = cs.validation_block(cs.DEFAULT_OUT)
    check("② validation_block 반환", isinstance(vb, dict) and "fold_stats" in vb)

    saved = me.LEDGER
    try:
        me.LEDGER = os.path.join(tmp, "ledger.jsonl")
        me.append_ledger({"id": "T1", "ts": "2026-10-05T03:00:00+09:00", "rc": 0, "parsed": {"metric_name": "unit"}})
        line = open(me.LEDGER, encoding="utf-8").read().strip().splitlines()[-1]
        rec = json.loads(line)
        check("② append_ledger 가 validation 블록 주입", isinstance(rec.get("validation"), dict),
              str(list((rec.get("validation") or {}).keys())))
        # ③ 트레이더 키워드 검사 (최근 5행 시뮬레이션)
        rows = [{"id": f"X{i}", "parsed": {"per_exp": {"a": 1}}} for i in range(4)] + [rec]
        has, gaps = trader_keyword_flags(rows)
        check("③ 트레이더 5키워드 전부 통과", not gaps, f"has={has} gaps={gaps}")
        # ④ 성적표가 없을 때: 예외 없이 블록 없이 기록
        empty = os.path.join(tmp, "none.json")
        check("④ 파일 없으면 None", cs.validation_block(empty) is None)
        me.append_ledger({"id": "T2", "rc": 0, "parsed": {}})
        last = json.loads(open(me.LEDGER, encoding="utf-8").read().strip().splitlines()[-1])
        check("④ 블록 없어도 기록은 남는다", last.get("id") == "T2")
    finally:
        me.LEDGER = saved

    # ⑤ 파서·판정기 배선(구동기)
    sp = me.summary_path("champion_scorecard", "python3 scripts/champion_scorecard.py --out %s" % cs.DEFAULT_OUT)
    check("⑤ summary_path 가 --out 을 따른다", sp == cs.DEFAULT_OUT, sp)
    # 절대경로(호스트) 그대로 · 상대경로는 PROJ 기준 · /app/... 는 컨테이너 매핑
    check("⑤ 상대 --out 은 PROJ 기준(=SCORECARD)",
          me.summary_path("champion_scorecard",
                          "python3 scripts/champion_scorecard.py --out data/reports/model_engineer_scorecard.json")
          == me.SCORECARD, "회귀 감시(2026-10-05 판정불가 사고)")
    check("⑤ /app/... --out 은 컨테이너 매핑",
          me.summary_path("champion_scorecard", "python3 x.py --out /app/reports/overnight/sc.json")
          == os.path.join(me.PROJ, "services/xgboost-ml/reports/overnight/sc.json"))
    check("⑤ --out 없으면 기본 SCORECARD",
          me.summary_path("champion_scorecard", "python3 scripts/champion_scorecard.py") == me.SCORECARD)
    parsed = me.parse_champion_scorecard(cs.DEFAULT_OUT, 0.0)
    check("⑤ 파서가 fold_stats 반환", isinstance(parsed.get("fold_stats"), dict) and parsed["fold_stats"].get("mean") is not None,
          str(parsed.get("error")))
    check("⑤ per_exp 를 만들지 않는다(스코어보드 오독 방지)", "per_exp" not in parsed)
    verdict, detail, delta = me.judge_champion_scorecard({"metric": "champion_scorecard"}, parsed)
    check("⑤ 판정 = 계약 충족", verdict == "계약 충족", f"{verdict} :: {detail[:120]}")
    # ⑥ 손상 파일은 판정불가(예외 금지)
    bad = os.path.join(tmp, "bad.json")
    open(bad, "w", encoding="utf-8").write("{not json")
    pbad = me.parse_champion_scorecard(bad, 0.0)
    check("⑥ 손상 JSON → error dict", bool(pbad.get("error")), str(pbad.get("error"))[:60])
    check("⑥ 손상 시 판정불가", me.judge_champion_scorecard({}, pbad)[0] == "판정불가")
    # ⑦ mtime 가드(옛 결과 오독 방지)
    stale = me.parse_champion_scorecard(cs.DEFAULT_OUT, os.path.getmtime(cs.DEFAULT_OUT) + 10)
    check("⑦ mtime 가드(미갱신 → error)", bool(stale.get("error")), str(stale.get("error"))[:40])

    print(f"\n{'ALL PASS' if not FAILS else 'FAILED: ' + ', '.join(FAILS)}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
