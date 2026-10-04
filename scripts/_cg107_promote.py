#!/usr/bin/env python3
"""CG107 을 needs_setup → pending 으로 승격(셋업 구현 완료).

구현·검증 실측(2026-10-05 04:1x):
  · `wf_wave.make_labels(..., thresh=None)` + kind='abs_thresh' 분기(결측 유지·중간 분위 미폐기).
  · `wf_label_sweep` 가 cfg['thresh'] 를 전달(다른 kind 는 None → 기존 경로 비트 동일).
  · AT_* 5개 config 등록(게이트 ON·절대임계 +0.02·h5·5서로소 구간) — 대조 US_* 같은 런.
  · 자체점검 `_abs_thresh_label_test.py` 9/9 PASS(경계·결측·도메인·국면적응·시점정합·폴백금지·회귀2종).
  · 기존 회귀 `_deployable_slice_config_test.py` ALL PASS(184 configs 파싱 — 신규 AT 포함).
  · 패널 panel_prod200.npz 존재(17.5MB·200종목·2026-10-03), gate ON 경로 재사용.
"""
import json

P = "/home/jhshi/analyist_dd/docs/QUANT_MODEL_BACKLOG.json"
d = json.load(open(P, encoding="utf-8"))
items = d["items"]
hit = 0
for it in items:
    if it.get("id") == "CG107":
        it["status"] = "pending"
        it["command"] = (
            "docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=4 timeout 7200 "
            "python -u scripts/wf_label_sweep.py --panel /app/app/models/wf/panel_prod200.npz "
            "--folds 5 --seeds 5 --only AT_00_30,AT_30_60,AT_60_90,AT_90_120,AT_120_150,"
            "US_00_30,US_30_60,US_60_90,US_90_120,US_120_150'")
        it["setup_needed"] = (
            "완료(2026-10-05 04:1x): kind='abs_thresh' 구현 + AT_* config + 자체점검 9/9 PASS + "
            "기존 config 회귀 ALL PASS. 남은 것은 실행뿐 — est_minutes 40 은 CG105 실측(8.6s/cell × "
            "250 cell)에서 산출했고 컨테이너 timeout 은 그 3배(7200s)로 잡았다.")
        it["evidence"] = (it.get("evidence", "") +
                          " ④ [2026-10-05 셋업 완료] make_labels(abs_thresh) 9/9 PASS · "
                          "config 파싱 회귀 ALL PASS(184) · panel_prod200 존재 확인.")
        hit += 1
assert hit == 1, f"CG107 을 1건 찾지 못함(hit={hit})"
d["items"] = items
d["updated_at"] = "2026-10-05T04:1x+09:00"
json.dump(d, open(P, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

chk = json.load(open(P, encoding="utf-8"))
cg = [i for i in chk["items"] if i["id"] == "CG107"][0]
print("CG107 status:", cg["status"], "| est_minutes:", cg.get("est_minutes"))
print("total items:", len(chk["items"]))
