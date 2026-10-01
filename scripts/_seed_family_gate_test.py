#!/usr/bin/env python3
"""자가점검: CG59 — 다중 시드 짝 판정 집계기/판정기 하드닝.

이 스택에는 pytest 가 없다 → 순수 파이썬 PASS/FAIL + sys.exit(1) 형태로 쓴다(호스트에서 실행).

왜(CG59, 2026-10-02): 위임 리뷰가 지적한 4개 구멍이 모두 **조용한 오판**으로 이어진다.
  ① 시드 수를 강제하지 않는다 → arm 하나가 중단돼 3/5 시드만 집계돼도 "3/3 신호있음"이 성립한다.
  ② 위치(i) 페어링 → 한쪽 arm 의 시드가 빠지거나 순서가 섞이면 **다른 유니버스끼리** Δ 를 잰다
     (시드 교체 잡음이 ±0.0133 로 사전문턱 +0.02 와 같은 크기였다 — CG48/49/CG55).
  ③ 두 arm 의 채점 창·폴드 수가 달라도 판정이 나온다(CG24: 2/5 폴드만 측정돼도 요약엔 안 드러남).
  ④ 동점(Δ=0)이 음수로 계수된다 → 부호검정이 보수적으로 뒤집힌다.

검증 항목:
  1. 동점 1개 포함 5시드 → 문턱 명목 초과지만 '노이즈'(부호 불일치) · n_ties 표기
  2. 3시드 + --expect-seeds 5 → 집계기 '판정불가' · 구동기 판정도 '판정불가'
  3. 두 arm 의 채점 창이 다름 → '판정불가(창 불일치)'
  4. 폴드 수 불일치 → '판정불가(폴드 수 불일치)'
  5. 정상 5/5 (전부 양(+)) → '신호있음'
  6. 시드 순서 뒤바뀜 → 시드 정체 페어링으로 3/3 (위치 페어링이면 2/3 — 오배선 검출)
  7. 시드 필드가 없으면 위치 페어링 폴백 + 경고(회귀 0)
  8. 회귀: 실제 cg58 산출물 재집계 수치가 기존 요약과 동일(Δ−0.0136 · 3/5)

실행: python3 scripts/_seed_family_gate_test.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import model_engineer_cycle as m  # noqa: E402

FAILS: list[str] = []
PASSES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASSES if cond else FAILS).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")


def eval_json(path: str, auc: float, model_dir: str, seed, windows=None, n_folds=3) -> None:
    """champion_robust_eval.py 요약 스키마를 모방한 JSON."""
    windows = windows or [["2025-12-01", "2026-01-28"], ["2026-01-29", "2026-03-31"],
                          ["2026-04-01", "2026-05-29"]]
    folds = [{"fold": i + 1, "window": list(windows[i % len(windows)]), "n_dates": 10,
              "auc_mean": round(auc - 0.01, 4), "auc_std": 0.02} for i in range(n_folds)]
    doc = {"measured_at": "2026-10-02T00:10:00", "model_dir": model_dir,
           "protocol": "3-fold 연속 시간창, 크로스섹션 AUC, purge=1거래일",
           "metric": "cross_sectional_auc_mean", "robust_auc": auc,
           "auc_std_across_folds": 0.02, "folds": folds, "auc_pooled": auc,
           "rows_scored": 1800}
    if seed is not None:
        doc["universe"] = {"mode": "training", "n": 60, "seed": seed}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)


def run_agg(tmp: str, base: list, cand: list, tag: str, seeds=None,
            extra=None, base_windows=None, cand_windows=None, base_folds=3, cand_folds=3):
    """base/cand = AUC 리스트. seeds=None 이면 universe 키를 아예 넣지 않는다(폴백 경로)."""
    bp, cp = [], []
    for i, a in enumerate(base):
        p = os.path.join(tmp, f"{tag}_b{i}.json")
        eval_json(p, a, "app/models/champion",
                  None if seeds is None else seeds[i], base_windows, base_folds)
        bp.append(p)
    for i, a in enumerate(cand):
        p = os.path.join(tmp, f"{tag}_c{i}.json")
        eval_json(p, a, "app/models/champion_cand",
                  None if seeds is None else seeds[i], cand_windows, cand_folds)
        cp.append(p)
    out = os.path.join(tmp, f"{tag}_agg.json")
    cmd = [sys.executable, os.path.join(HERE, "champion_seed_family_agg.py"),
           "--agg-out", out, "--arm", "champ", *bp, "--arm", "cand", *cp]
    if extra:
        cmd += extra
    r = subprocess.run(cmd, capture_output=True, text=True)
    doc = json.load(open(out, encoding="utf-8")) if os.path.exists(out) else None
    return r.returncode, (r.stdout + r.stderr), doc


def judge(path: str, command: str = "python x.py"):
    item = {"id": "CG59T", "metric": "champion_seed_family", "command": command,
            "success": "짝 Δ ≥ +0.02 · 양(+) 전부"}
    return m.judge_seed_family(item, m.parse_champion_seed_family(path, 0.0))


def main() -> int:
    tmp = tempfile.mkdtemp()
    try:
        print("== 1. 동점 1개 포함 5시드 → 노이즈(부호 불일치) · n_ties")
        rc, out, doc = run_agg(tmp, [0.50, 0.50, 0.50, 0.50, 0.50],
                               [0.53, 0.53, 0.50, 0.53, 0.53], "tie", seeds=[0, 1, 2, 3, 4])
        p = (doc or {}).get("paired", {})
        check("집계기 rc=0", rc == 0, out.strip()[-160:])
        check("delta_mean == +0.0240 (문턱 초과)", p.get("delta_mean") == 0.024,
              str(p.get("delta_mean")))
        check("n_ties == 1", p.get("n_ties") == 1, str(p.get("n_ties")))
        check("pos_seeds == 4/5", p.get("pos_seeds") == "4/5", str(p.get("pos_seeds")))
        check("verdict 에 '동점 1' 표기", "동점 1" in str(p.get("verdict")), str(p.get("verdict")))
        v, d, _ = judge(os.path.join(tmp, "tie_agg.json"))
        check("구동기 판정 = 노이즈", v == "노이즈", f"{v} | {d}")
        check("구동기 detail 에 동점 표기", "동점 1" in d, d[:150])

        print("== 2. 3시드 + --expect-seeds 5 → 판정불가")
        _, _, doc2 = run_agg(tmp, [0.50, 0.50, 0.50], [0.53, 0.53, 0.53], "exp3",
                             seeds=[0, 1, 2], extra=["--expect-seeds", "5"])
        p2 = (doc2 or {}).get("paired", {})
        check("집계 verdict = 판정불가(기대 시드 수 미달)",
              str(p2.get("verdict", "")).startswith("판정불가") and "기대 시드 수" in str(p2.get("verdict")),
              str(p2.get("verdict")))
        check("expect_seeds 가 요약에 기록", p2.get("expect_seeds") == 5, str(p2.get("expect_seeds")))
        # 같은 산출물을 --expect-seeds 5 가 붙은 커맨드로 구동기 판정 → 강제
        v2, d2, _ = judge(os.path.join(tmp, "exp3_agg.json"), "python agg.py --expect-seeds 5")
        check("구동기 판정 = 판정불가", v2 == "판정불가", f"{v2} | {d2}")
        check("사유에 3<5 표기", "3<5" in d2, d2[:150])

        print("== 3. 채점 창 불일치 → 판정불가")
        alt = [["2025-11-01", "2025-12-30"], ["2026-01-29", "2026-03-31"], ["2026-04-01", "2026-05-29"]]
        _, _, doc3 = run_agg(tmp, [0.50, 0.50, 0.50], [0.53, 0.53, 0.53], "win",
                             seeds=[0, 1, 2], cand_windows=alt)
        p3 = (doc3 or {}).get("paired", {})
        check("집계 verdict = 판정불가(창)", str(p3.get("verdict", "")).startswith("판정불가")
              and "창" in str(p3.get("verdict")), str(p3.get("verdict")))
        v3, d3, _ = judge(os.path.join(tmp, "win_agg.json"))
        check("구동기 판정 = 판정불가", v3 == "판정불가", f"{v3} | {d3}")
        check("사유에 '창' 표기", "창" in d3, d3[:150])

        print("== 4. 폴드 수 불일치 → 판정불가")
        _, _, doc4 = run_agg(tmp, [0.50, 0.50, 0.50], [0.53, 0.53, 0.53], "fold",
                             seeds=[0, 1, 2], cand_folds=5)
        p4 = (doc4 or {}).get("paired", {})
        # 창 목록도 함께 달라지므로(폴드 수가 다르면 창 목록 길이도 다르다) 사유는 '창'으로
        # 먼저 잡힐 수 있다 — 판정 자체가 '판정불가'인 것과 fold_mismatch 검출을 각각 본다.
        check("집계 verdict = 판정불가", str(p4.get("verdict", "")).startswith("판정불가"),
              str(p4.get("verdict")))
        check("fold_mismatch 검출", len(p4.get("fold_mismatch") or []) > 0,
              str(p4.get("fold_mismatch")))
        v4, d4, _ = judge(os.path.join(tmp, "fold_agg.json"))
        check("구동기 판정 = 판정불가", v4 == "판정불가", f"{v4} | {d4}")

        print("== 4b. 창은 같은데 폴드 수만 다름 → 폴드 사유로 판정불가")
        # 같은 창 1개를 반복해 쓰는 arm(폴드 5) vs 3폴드 arm — 창 시그니처를 같게 만들 수 없으므로
        # 집계기의 fold_mismatch 경로를 직접 확인한다(요약을 손으로 만들어 판정기만 태운다).
        fake = {"paired": {"n": 3, "threshold": 0.02, "delta_mean": 0.03, "se": 0.0, "t": None,
                           "pos_seeds": "3/3", "pos_frac": 1.0, "n_ties": 0,
                           "fold_mismatch": ["seed 0: folds 3 vs 5"], "window_mismatch": []},
                "arms": {"champ": {"mean": 0.50}, "cand": {"mean": 0.53}}}
        fp = os.path.join(tmp, "fold_only.json")
        with open(fp, "w", encoding="utf-8") as f:
            json.dump(fake, f, ensure_ascii=False)
        v4b, d4b, _ = judge(fp)
        check("구동기 판정 = 판정불가(폴드 사유)", v4b == "판정불가" and "폴드" in d4b,
              f"{v4b} | {d4b}")

        print("== 5. 정상 5/5 → 신호있음")
        _, _, doc5 = run_agg(tmp, [0.50, 0.51, 0.52, 0.53, 0.54],
                             [0.53, 0.54, 0.55, 0.56, 0.57], "sig5", seeds=[0, 1, 2, 3, 4])
        p5 = (doc5 or {}).get("paired", {})
        check("delta_mean == +0.0300", p5.get("delta_mean") == 0.03, str(p5.get("delta_mean")))
        check("pos_seeds == 5/5", p5.get("pos_seeds") == "5/5", str(p5.get("pos_seeds")))
        check("pairing == universe.seed", p5.get("pairing") == "universe.seed", str(p5.get("pairing")))
        check("verdict = 신호있음", str(p5.get("verdict", "")).startswith("신호있음"),
              str(p5.get("verdict")))
        v5, d5, _ = judge(os.path.join(tmp, "sig5_agg.json"), "python agg.py --expect-seeds 5")
        check("구동기 판정 = 신호있음", v5 == "신호있음", f"{v5} | {d5}")

        print("== 6. 시드 순서 뒤바뀜 → 시드 정체 페어링(위치 페어링이면 오배선)")
        # 시드별 Δ 는 전부 +0.03 이지만 **파일 순서를 arm 마다 다르게** 준다.
        #   champ 파일 순서 = s2, s0, s1  → [0.60, 0.50, 0.55]
        #   cand  파일 순서 = s0, s1, s2  → [0.53, 0.58, 0.63]
        # 시드 페어링이면 Δ = +0.03 ×3 (3/3), 위치 페어링이면 [−0.07, +0.08, +0.08] (2/3).
        bp, cp = [], []
        for seed, auc in [(2, 0.60), (0, 0.50), (1, 0.55)]:
            p = os.path.join(tmp, f"shuf_b{seed}.json")
            eval_json(p, auc, "app/models/champion", seed)
            bp.append(p)
        for seed, auc in [(0, 0.53), (1, 0.58), (2, 0.63)]:
            p = os.path.join(tmp, f"shuf_c{seed}.json")
            eval_json(p, auc, "app/models/champion_cand", seed)
            cp.append(p)
        out6 = os.path.join(tmp, "shuf_agg.json")
        r6 = subprocess.run([sys.executable, os.path.join(HERE, "champion_seed_family_agg.py"),
                             "--agg-out", out6, "--arm", "champ", *bp, "--arm", "cand", *cp],
                            capture_output=True, text=True)
        doc6 = json.load(open(out6, encoding="utf-8")) if os.path.exists(out6) else {}
        p6 = (doc6 or {}).get("paired", {})
        check("시드 페어링 사용", p6.get("pairing") == "universe.seed", str(p6.get("pairing")))
        check("per_seed_delta 3/3 (위치 페어링이면 2/3)",
              p6.get("pos_seeds") == "3/3", f"{p6.get('pos_seeds')} deltas={p6.get('per_seed_delta')}")
        check("시드별 Δ 전부 +0.03", p6.get("per_seed_delta") == [0.03, 0.03, 0.03],
              str(p6.get("per_seed_delta")))
        check("정렬된 시드 목록이 base 순서(2,0,1)",
              [r.get("seed") for r in (doc6 or {}).get("seeds", [])] == [2, 0, 1],
              str([r.get("seed") for r in (doc6 or {}).get("seeds", [])]))

        print("== 7. 시드 필드 없음 → 위치 페어링 폴백 + 경고(회귀 0)")
        rc7, out7, doc7 = run_agg(tmp, [0.50, 0.52, 0.48], [0.53, 0.55, 0.51], "noseed")
        p7 = (doc7 or {}).get("paired", {})
        check("pairing == positional", p7.get("pairing") == "positional", str(p7.get("pairing")))
        check("경고에 '위치 페어링' 포함", "위치 페어링" in out7, out7.strip()[-160:])
        check("delta_mean == +0.0300(수치 회귀 없음)", p7.get("delta_mean") == 0.03,
              str(p7.get("delta_mean")))
        check("verdict = 신호있음", str(p7.get("verdict", "")).startswith("신호있음"),
              str(p7.get("verdict")))

        print("== 8. 실측 cg58 산출물 재집계 회귀(Δ−0.0136 · 3/5)")
        root = os.path.dirname(HERE)
        rep = os.path.join(root, "services", "xgboost-ml", "app", "reports")
        prevs = [os.path.join(rep, f"cg58_prev_s{i}.json") for i in range(5)]
        news = [os.path.join(rep, f"cg58_new_s{i}.json") for i in range(5)]
        if all(os.path.exists(p) for p in prevs + news):
            out8 = os.path.join(tmp, "cg58_reagg.json")
            r8 = subprocess.run([sys.executable, os.path.join(HERE, "champion_seed_family_agg.py"),
                                 "--agg-out", out8, "--expect-seeds", "5",
                                 "--arm", "prev", *prevs, "--arm", "new", *news],
                                capture_output=True, text=True)
            d8 = json.load(open(out8, encoding="utf-8")) if os.path.exists(out8) else {}
            p8 = d8.get("paired", {})
            old = json.load(open(os.path.join(rep, "cg58_summary.json"), encoding="utf-8"))["paired"]
            check("재집계 delta_mean 동일", p8.get("delta_mean") == old.get("delta_mean"),
                  f"{p8.get('delta_mean')} vs {old.get('delta_mean')}")
            check("재집계 pos_seeds 동일", p8.get("pos_seeds") == old.get("pos_seeds"),
                  f"{p8.get('pos_seeds')} vs {old.get('pos_seeds')}")
            check("재집계 verdict = 노이즈", str(p8.get("verdict", "")).startswith("노이즈"),
                  str(p8.get("verdict")))
            check("pairing == universe.seed", p8.get("pairing") == "universe.seed",
                  str(p8.get("pairing")))
            check("시드 누락 없음(5/5)", p8.get("n") == 5 and not p8.get("pairing_warnings"),
                  f"n={p8.get('n')} warn={p8.get('pairing_warnings')}")
        else:
            check("cg58 산출물 존재", False, "services/xgboost-ml/app/reports/cg58_*.json 없음")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n총 {len(PASSES)} PASS / {len(FAILS)} FAIL")
    for f in FAILS:
        print("  FAIL:", f)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
