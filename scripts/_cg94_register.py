#!/usr/bin/env python3
"""CG93 커맨드 수리 + CG94(검정력 보강 재측정) pending 등록 — 2026-10-04 05:0x."""
import json, os

p = "docs/QUANT_MODEL_BACKLOG.json"
d = json.load(open(p, encoding="utf-8"))
by = {i["id"]: i for i in d["items"]}

# ── 1) CG93 커맨드 수리: 마지막 집계 단계를 컨테이너 안에서 돌린다 ──────────────
cg93 = by["CG93"]
old_tail = ("&& python3 scripts/topk_precision.py services/xgboost-ml/reports/overnight/cg93_preds.jsonl "
            "--k 3,5,10 --arm cg92_q05 --control cg92_q30 --restrict-q 0.05 --min-pool 20 "
            "--json-out /app/reports/overnight/cg93_topk.json")
new_tail = ("&& docker exec stock_xgboost_ml sh -c 'cd /app && python scripts/topk_precision.py "
            "/app/reports/overnight/cg93_preds.jsonl --k 3,5,10 --arm cg92_q05 --control cg92_q30 "
            "--restrict-q 0.05 --min-pool 20 --json-out /app/reports/overnight/cg93_topk.json'")
assert old_tail in cg93["command"], "CG93 커맨드 꼬리를 못 찾음"
cg93["command"] = cg93["command"].replace(old_tail, new_tail)
cg93["note"] += (
    " ⚠ [수리 2026-10-04 05:0x] 종전 커맨드의 마지막 집계는 **호스트** python3 로 `/app/...` json-out 을 "
    "받아 `os.makedirs('/app')` 에서 PermissionError(rc=1)로 죽었다 — 계산(평가 2회)은 완료돼 preds 가 "
    "남았고 컨테이너에서 재집계해 --ingest 로 편입했다. 집계 단계는 컨테이너 안에서 돌려라"
    "(호스트 python3 + 컨테이너 경로 = 실패).")


def ev(mdir, q, tag, stock):
    return ("docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=4 timeout 7200 python "
            "scripts/champion_robust_eval.py --model-dir app/models/wf/%(m)s --label-kind quantile "
            "--label-q %(q)s --horizon 5 --folds 10 --dates-per-fold 8 --stocks %(n)s --universe training "
            "--universe-seed 0 --train-start 2025-08-22 --train-end 2025-11-20 "
            "--dump-preds /app/reports/overnight/cg94_q%(t)s_preds.jsonl --dump-tag %(md)s "
            "--out /app/reports/overnight/cg94_q%(t)s_robust.json'" % {
                "m": mdir, "q": q, "t": tag, "md": mdir, "n": stock})


cg94 = {
    "id": "CG94",
    "priority": 1,
    "affects_model": True,
    "title": ("생산 경로 라벨 꼬리 실질성 — 검정력 보강 재측정 (300종목·10폴드×8일 · 공통 후보집합 top-k) "
              "[라벨 꼬리 축 최종 관문]"),
    "hypothesis": (
        "CG93 의 사전등록 미충족은 '효과 없음'이 아니라 **검정력 부족**이다 — 같은 런·같은 행의 공통 "
        "후보집합(restrict-q 0.05)에서 k=3 Δprec **+0.1667**(p 0.0094)로 강하게 통과했고 k=5 도 Δprec "
        "**+0.0600** 으로 크기 문턱(+0.05)은 넘겼으나 동점 18/40·유효 22셀이라 부호검정 p=0.2863 에서 "
        "멈췄다. 같은 계측기의 스윕 경로(CG90)는 k=3 +0.0815 · k=5 +0.0587 로 **둘 다 p<1e-4** 였다. "
        "생산 경로의 셀 수와 후보풀을 키우면(40→80셀 · 200→300종목 ⇒ 셀당 후보풀 중앙값 ≈12→≈18) 동점이 "
        "줄어 k=5 를 결정할 수 있다. 모델은 CG92 산출물을 그대로 재사용하므로 **재학습 0** — 순수 평가셋 "
        "확대다."),
    "metric": "topk_precision",
    "command": " && ".join([
        ev("cg92_q05", "0.05", "05", 300),
        ev("cg92_q30", "0.30", "30", 300),
        "docker exec stock_xgboost_ml sh -c 'cat /app/reports/overnight/cg94_q05_preds.jsonl "
        "/app/reports/overnight/cg94_q30_preds.jsonl > /app/reports/overnight/cg94_preds.jsonl'",
        "docker exec stock_xgboost_ml sh -c 'cd /app && python scripts/topk_precision.py "
        "/app/reports/overnight/cg94_preds.jsonl --k 3,5,10 --arm cg92_q05 --control cg92_q30 "
        "--restrict-q 0.05 --min-pool 20 --json-out /app/reports/overnight/cg94_topk.json'",
    ]),
    "counterfactual": ("cg92_q30 (같은 창·같은 유니버스·같은 행 원본의 q0.30 arm — 모델 디렉터리 재사용, "
                       "재학습 없음)"),
    "success": (
        "CG90/CG93 과 **동일한** 사전등록 기준을 재사용한다(기준 이동 아님): 공통 후보집합(restrict-q 0.05)"
        "에서 k=3 **과** k=5 둘 다 Δprec ≥ +0.05 이고 동점제외 부호검정 p<0.05, Δfwd_ret>0 → 라벨 꼬리 "
        "레버가 생산 경로에서도 실질성 있음 → 리뷰보드 승인 요청(라벨 정의 변경) + 소비 정책 동반 요청"
        "(CG82: 절대문턱 → 분위 top-k). 미충족이면 **라벨 꼬리 축을 최종 종결**하고 데이터 축으로 복귀한다"
        "(방향 무관, 이 항목의 결과로 축을 닫는다)."),
    "expected": ("셀이 2배(40→80)·풀 중앙값 1.5배(12→18)라 동점 비율이 45%→~30% 로 내려가면 유효셀이 "
                 "22→~56 이 되어 같은 효과크기(+0.06)에서 부호검정 p≈0.02 로 내려간다. k=3 통과는 재현될 "
                 "것이고 k=5 가 관건이다."),
    "cost": ("평가 2회(모델 재사용, 학습 0). 실측 200종목×40일 = 6.7 builds/s → 20분/arm 이었으므로 "
             "300종목×80일 = 24,000 builds/arm ≈ 60분/arm → 총 ~125분."),
    "est_minutes": 130,
    "status": "pending",
    "note": (
        "2026-10-04 05:0x 신설(주말 자율 세션). 계기: CG93 이 k=3 은 통과(+0.1667 p=0.0094)하고 k=5 는 "
        "크기만 통과(+0.0600 p=0.2863)해 **사전등록 이분법이 회색지대에 걸렸다** — 그대로 닫으면 '효과 "
        "없음'으로, 그대로 열면 '기준 무시'가 된다. 그래서 기준은 그대로 두고 **검정력만** 올린 마지막 "
        "측정을 등록한다. ⚠ 모델은 app/models/wf/cg92_q05·cg92_q30 재사용(재학습 없음) — 라벨 정의가 "
        "다르면 비교가 성립하지 않으므로 두 arm 을 반드시 같은 커맨드에서 연속 실행해 창을 고정한다. "
        "⚠ --stocks 300 은 평가셋만 확대한다(학습은 200종목·2025-08-22~11-20) — 두 arm 이 같은 행을 "
        "채점하므로 짝 Δ 의 귀속은 유지되지만 CG92/CG93 과 절대값을 직접 비교하지는 말 것(유니버스 상이). "
        "⚠ --folds 10 창은 각 ~20거래일이고 전부 학습구간 이후다(windows_excluded=[]). ⚠ 마지막 집계 "
        "단계는 **컨테이너 안**에서 돌려라(호스트 python3 + /app 경로 = PermissionError, CG93 실측)."),
}
assert "CG94" not in by, "CG94 이미 존재"
d["items"].append(cg94)
json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

chk = json.load(open(p, encoding="utf-8"))
ids = [i["id"] for i in chk["items"]]
assert "CG94" in ids, "CG94 등록 실패"
print("OK CG94 registered | total", len(chk["items"]), "| pending:",
      sum(1 for i in chk["items"] if i.get("status") == "pending"))
