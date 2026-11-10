#!/usr/bin/env python3
"""2026-11-10 19:1x — CG180 판정 기록 + CG183 측정사슬 수리(백로그 갱신).

배경(실측):
  · CG180 rc=0: robust 0.5706±0.0994, 폴드 2개(0.4713 / 0.6700), 순기대 −0.160%p/세션.
    폴드 2개·std 0.0994 로 사전문턱 판정 불가 → '노이즈(참조값)'로 기록.
  · CG183 은 metric=expectancy_pct_per_session 이었으나 **구동기에 그 파서가 없다**(grep 0건)
    → 90분을 돌려도 '판정불가'로 남는다. 또한 후보는 데이터 창 끝(2026-11-09)까지 학습하므로
    data_end 를 학습 종료로 읽으면 모든 평가창이 '겹침 제외'되어 OOS 가 0 이 된다.
  수리: retrain_champion 이 fit_end/val_start 기록 → evaluator 가 fit_end 우선 →
        래퍼(scripts/cg183_retrain_audit.sh)가 재학습 후 val_start 이후를 3폴드로 쪼개
        챔피언과 후보를 **같은 런·같은 창**에서 동시 채점(metric=robust_oos_audit, model_label=cand).
"""
import json
from datetime import datetime

P = "docs/QUANT_MODEL_BACKLOG.json"
b = json.load(open(P, encoding="utf-8"))
items = b["items"] if isinstance(b, dict) and "items" in b else b
by_id = {it["id"]: it for it in items}
ts = datetime.now().isoformat(timespec="minutes")
stamp = ts[:16].replace("T", " ")

# ── CG180 판정 노트 ────────────────────────────────────────────────
cg180 = by_id["CG180"]
cg180["note"] = (cg180.get("note", "") +
    f" | {stamp} 엔지니어 판정: **노이즈(참조값)** — 폴드 2개(0.4713/0.6700)·std 0.0994 는 "
    "사전문턱 +0.02 보다 5배 큰 분산이라 '개선'도 '악화'도 주장할 수 없다(사전등록 ③ 이 "
    "이미 '참조로만'이라 못박음). 다만 날짜별 AUC 가 0.3694~0.7425 로 갈리고 순기대 "
    "−0.160%p/세션(t −0.72)·앞 −0.062/뒤 −0.256(neither) 이므로 **신선 구간에서도 챔피언 "
    "우위는 없다**는 방향성은 유지된다. 재기준선을 5폴드로 만들려면 OOS 자체가 길어야 하고, "
    "그건 재학습(CG183) 이후에만 가능하다 → 축은 CG183 으로 이관.")

# ── CG183 측정사슬 수리 ────────────────────────────────────────────
cg = by_id["CG183"]
cg["metric"] = "robust_oos_audit"
cg["model_label"] = "cand"
cg["counterfactual_model"] = "champ"
cg["command"] = ("docker exec stock_xgboost_ml sh -c 'cd /app && timeout 9000 "
                 "sh scripts/cg183_retrain_audit.sh'")
cg["est_minutes"] = 150
cg["success"] = (
    "① 재학습 완료(3모델 저장 · feature_names 173 = 배포 계약 · 메타에 fit_end/val_start 기록) "
    "② 같은 런·같은 창 채점: val_start 이후 3폴드×5거래일에서 후보 폴드 평균 − 챔피언 폴드 평균 "
    "≥ +0.02 (폴드승률 3/3) ③ 순기대(체결성 필터·수수료 왕복 0.21%p) 후보 > 챔피언 +0.1%p/세션 "
    "— ②③ 모두 충족해야 신호. AUC 만 오르면 승격 금지·보류 기록. 승격은 이 루프에서 하지 않는다.")
cg["command_verified_at"] = f"{ts} (래퍼 인용 프로브 rc=0: 2-모델 JSON 파싱·load_model 도달 확인)"
cg["note"] = (cg.get("note", "") +
    f" | {stamp} 엔지니어 측정사슬 수리: ① metric 이 expectancy_pct_per_session 이었는데 구동기에 "
    "그 파서가 **없다**(grep 0건) → 90분 뒤 '판정불가' 확정이었다. ② 후보는 2026-11-09 까지 "
    "학습하므로 data_end 기준 _split_oos 는 전 창을 제외해 OOS 0. 수리: retrain_champion 이 "
    "fit_end(마지막 적합일)·val_start(검증 첫날)를 메타에 기록 → champion_robust_eval._meta_range "
    "가 fit_end 우선 → 래퍼가 val_start 이후만 3폴드로 쪼개 champ·cand 동시 채점. "
    "회귀: scripts/_meta_range_fitend_test.py 6/6 PASS. ⚠ val 은 early stopping 에 쓰였으므로 "
    "'완전 미사용'이 아니다 — 보고에 병기. ⚠ 학습창이 120일→약 85거래일로 짧아진다(창 축소 대가).")

json.dump(b, open(P, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("updated:", cg["id"], cg["status"], "|", cg["metric"], "| est", cg["est_minutes"])
print("CG180 note len:", len(cg180["note"]))
