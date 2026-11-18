#!/usr/bin/env python3
"""사전등록 '판정불가' 가드 자체점검 — 같은 런 대조군도 기록 기준선도 없는 스윕을 잡는가.

배경(실측 2026-11-18): 최근 40판정 중 8건이 verdict='기준선없음' 으로 닫혔다. 전부
`wf_label_sweep --only <q30 arm 들>` 만 돌려 (arm − 같은런 대조군) 도 (arm − 기록 기준선) 도
만들 수 없었던 등록이다. 10~60분 CPU 를 쓰고도 판정이 원천 불가였다.

이 테스트는 ① 잡아야 할 등록을 잡는가 ② 잡으면 안 되는 등록(같은 런 짝·기록 기준선·선별 없음)을
조용히 넘기는가 를 컨테이너 없이 검증한다.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model_engineer_cycle as M  # noqa: E402

FAIL = 0


def check(name, got, want):
    global FAIL
    ok = (bool(got) == want) if isinstance(want, bool) else (got == want)
    print(f"{'PASS' if ok else 'FAIL'} {name}: got={got!r} want_warn={want}")
    if not ok:
        FAIL += 1


SWEEP = ("docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=2 timeout 7200 "
         "python -u scripts/wf_label_sweep.py --panel /app/app/models/wf/panel_200liq_ffill.npz "
         "--folds 10 --seeds 5 --only {only} --out /app/reports/overnight/x.jsonl'")

# ① 잡아야 한다 — CG336 실등록 그대로(q30 arm 3개, 대조군 없음, baseline 없음)
cg336 = {"id": "CG336", "status": "pending",
         "command": SWEEP.format(only="LS_quant_q30_h5,LS_quant_q30_h8,LS_quant_q30_h10"),
         "arm": "LS_quant_q30_h5",
         "counterfactual": "LS_quant_q30_h5 (기본 분위 0.3 대조)"}
check("CG336 실등록", M.prereg_control_gap(cg336), True)

# ② 단일 arm + baseline 없음
single = {"id": "X1", "command": SWEEP.format(only="A_h5"), "arm": "A_h5",
          "counterfactual": "A_h5"}
check("단일 arm·기준선없음", M.prereg_control_gap(single), True)

# ③ 잡지 말아야 한다 — 대조군이 같은 런 --only 에 있음
paired = {"id": "X2", "command": SWEEP.format(only="CG323_ctl_h5,CG323_ss_h5"),
          "arm": "CG323_ss_h5", "counterfactual": "CG323_ctl_h5 (스무딩 없음)"}
check("같은 런 대조군 존재", M.prereg_control_gap(paired), False)

# ④ 잡지 말아야 한다 — --force-paired 트윈(__ff)
twin = {"id": "CG342", "command": SWEEP.format(only="LS_quant_q30_h5,LS_quant_q30_h5__ff"),
        "arm": "LS_quant_q30_h5__ff", "counterfactual": "같은 런 arm 쌍둥이"}
check("force-paired 트윈", M.prereg_control_gap(twin), False)

# ⑤ 잡지 말아야 한다 — 기록 기준선이 등록돼 있음
withbase = {"id": "X3", "command": SWEEP.format(only="LS_quant_q30_h5"),
            "arm": "LS_quant_q30_h5", "counterfactual": "CO_core30_h5",
            "baseline": {"value": 0.5406, "source": "quant_scoreboard"}}
check("기록 기준선 등록됨", M.prereg_control_gap(withbase), False)

# ⑥ 잡지 말아야 한다 — --only 자체가 없는(전 config 스윕) 등록
allsweep = {"id": "X4", "command": "docker exec stock_xgboost_ml sh -c 'cd /app && python -u "
            "scripts/wf_wave.py --days 420 --limit 200'", "arm": None, "counterfactual": ""}
check("선별 없음(전 config)", M.prereg_control_gap(allsweep), False)

print(f"\n{'ALL PASS' if FAIL == 0 else f'{FAIL} FAILED'}")
sys.exit(1 if FAIL else 0)
