#!/usr/bin/env python3
"""자체점검 — protocol_pair_anchor 배선 (CG145/CG146, 2026-10-09).

왜: CG146 을 틱이 시작한 직후 구동기가 `알 수 없는 metric 'protocol_pair_anchor' — 요약 경로
없음(판정불가로 기록)` 을 경고했다. 이 배선(summary_path·parse·judge)이 없으면 12런(≈54분)짜리
실측이 **판정 없이** 원장에 남는다 = CG143(파서만 있고 judge 분기 없음)과 동형 사고.

검사:
  1) summary_path 가 커맨드의 TAGPFX env 를 읽어 <TAGPFX>_pair_anchor.json 을 돌려준다
  2) parse_protocol_pair_anchor 가 정상 JSON 을 파싱하고, 런 실패·낡은 요약을 거부한다
  3) judge_protocol_pair_anchor 가 verdict 를 노출하고 delta=None(개선 카운터 무관)을 돌려준다
  4) per_exp 를 만들지 않는다(scoreboard 오독 방지)

실행: python3 scripts/_pair_anchor_judge_test.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model_engineer_cycle as me  # noqa: E402

FAILS = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        FAILS.append(name)


# ── A) summary_path 배선 — TAGPFX env 추출 ────────────────────────────────
CMD = ('ANCHORS="d0:2026-10-07 d1:2026-10-06" TAGPFX=cg146 bash '
       '/home/jhshi/analyist_dd/scripts/cg145_pair_anchor.sh')
sp = me.summary_path("protocol_pair_anchor", CMD)
check("summary_path: TAGPFX=cg146 → cg146_pair_anchor.json", sp.endswith("cg146_pair_anchor.json"))
check("summary_path: 경로가 overnight 밑", "reports/overnight" in sp)
sp_def = me.summary_path("protocol_pair_anchor", "bash scripts/cg145_pair_anchor.sh")
check("summary_path: TAGPFX 없음 → 기본 cg145", sp_def.endswith("cg145_pair_anchor.json"))
check("_env_arg: 따옴표 값에서 여는 따옴표 제거", me._env_arg(CMD, "ANCHORS") == "d0:2026-10-07")
check("_env_arg: --summary-out 류 오탐 없음", me._env_arg("cmd --out=TAGPFX=x", "TAGPFX") == "")

# ── B) 실측 요약 JSON(CG145) 전 구간 ──────────────────────────────────────
real = "services/xgboost-ml/reports/overnight/cg145_pair_anchor.json"
if os.path.exists(real):
    pr = me.parse_protocol_pair_anchor(real, 0.0)
    check("실측 파서: error 없음", not pr.get("error"))
    check("실측 파서: pair_delta 3앵커", len(pr.get("pair_delta") or {}) == 3)
    check("실측 파서: per_exp 없음(scoreboard 오독 방지)", "per_exp" not in pr)
    v, d, delta = me.judge_by_metric({"metric": "protocol_pair_anchor", "id": "CG145"}, pr)
    check("실측 판정: verdict 노출", v != "판정불가" and v.startswith("단일앵커 판정 갈림"))
    check("실측 판정: delta=None(개선 카운터 무관)", delta is None)
    check("실측 판정: detail 에 짝 Δ·문턱통과·부호", "+0.0055" in d and "2/3" in d and "True" in d)
    print(f"  실측 판정: {v}")
else:
    print("SKIP 실측 cg145_pair_anchor.json 없음(호스트 경로)")

# ── C) 합성 JSON — 정상 / 런 실패 / 낡은 요약 ─────────────────────────────
with tempfile.TemporaryDirectory() as td:
    p = os.path.join(td, "synth.json")
    json.dump({"metric": "protocol_pair_anchor",
               "anchors": {"d0": "2026-10-07", "d1": "2026-10-06"}, "arms": {"champ": "x", "cand": "y"},
               "config": "folds=5 stocks=60", "auc": {"d0/champ": 0.51, "d0/cand": 0.52,
                                                       "d1/champ": 0.50, "d1/cand": 0.53},
               "folds": {"d0/champ": [0.5, 0.52]}, "pair_delta": {"d0": 0.01, "d1": 0.03},
               "pair_delta_mean": 0.02, "pair_delta_std": 0.0141, "level_std": {"champ": 0.01},
               "anchors_over_threshold": 1, "anchor_threshold_pass": ["d0:x", "d1:O"],
               "sign_consistent": True, "pre_registered_threshold": 0.02,
               "verdict": "단일앵커 판정 갈림", "errors": {}}, open(p, "w"))
    pc = me.parse_protocol_pair_anchor(p, 0.0)
    check("합성 파서: 정상 파싱", not pc.get("error") and pc["threshold"] == 0.02)
    vc, dc, dlc = me.judge_by_metric({"metric": "protocol_pair_anchor", "id": "S1"}, pc)
    check("합성: verdict 노출", vc == "단일앵커 판정 갈림" and dlc is None)
    check("합성: detail 에 평균 부호 표기", "+0.0200" in dc)
    # 부호 불일치 verdict 도 그대로 노출
    v2, _, _ = me.judge_by_metric({"metric": "protocol_pair_anchor", "id": "S2"},
                                  dict(pc, verdict="부호 불일치 — 앵커별 Δ ..."))
    check("합성: 부호 불일치 노출", v2.startswith("부호 불일치"))
    # 런 실패 스키마 → 판정불가
    json.dump({"metric": "protocol_pair_anchor", "anchors": {"d0": "2026-10-07"},
               "errors": {"d0/cand": "TimeoutError"}}, open(p, "w"))
    pf = me.parse_protocol_pair_anchor(p, 0.0)
    check("합성 파서: 런 실패 → error", bool(pf.get("error")))
    vf, df, _ = me.judge_by_metric({"metric": "protocol_pair_anchor", "id": "S3"}, pf)
    check("합성: 런 실패 → 판정불가", vf == "판정불가" and "런 실패" in df)
    # mtime floor → 낡은 요약 거부
    stale = me.parse_protocol_pair_anchor(p, os.path.getmtime(p) + 10)
    check("합성 파서: 낡은 요약 거부", "미갱신" in str(stale.get("error")))

# ── D) 파일 없음 ──────────────────────────────────────────────────────────
v0, d0, dl0 = me.judge_by_metric({"metric": "protocol_pair_anchor", "id": "S4"},
                                 me.parse_protocol_pair_anchor("", 0.0))
check("파일 없음 → 판정불가", v0 == "판정불가" and d0 == "요약 파일 없음" and dl0 is None)
v9, _, _ = me.judge_by_metric({"metric": "protocol_pair_anchor", "id": "S5"},
                              {"error": "요약 파일 없음", "path": "/x"})
check("빈 경로 → 판정불가", v9 == "판정불가")

print(f"\n결과: FAIL={len(FAILS)}" + (f" {FAILS}" if FAILS else " (전부 PASS)"))
sys.exit(1 if FAILS else 0)
