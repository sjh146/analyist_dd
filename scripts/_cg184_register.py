import json
import datetime

p = 'docs/QUANT_MODEL_BACKLOG.json'
d = json.load(open(p, encoding='utf-8'))
items = d['items']
assert not any(i.get('id') == 'CG184' for i in items), 'CG184 already exists'

now = datetime.datetime.now().astimezone().isoformat(timespec='seconds')
cmd = ("docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=4 timeout 27300 "
       "python -u scripts/wf_label_sweep.py --panel /app/app/models/wf/panel_150d_fresh.npz "
       "--days 150 --end-date 2026-11-09 --limit 50 --folds 5 --seeds 5 "
       "--only CO_core30_h5,CO_rank_smooth_d1_h5 "
       "--summary-out /app/reports/overnight/cg184_fresh_panel_summary.json'")

item = {
    "id": "CG184",
    "priority": 2,
    "status": "pending",
    "kind": "experiment",
    "owner": "engineer",
    "affects_model": True,
    "title": "신선 구간(2026-09-24~11-09) 포함 단기 패널 빌드 + 게이트 arm in-run A/B — 데이터 축 직접 검증",
    "hypothesis": (
        "10-11~11-09 호스트 오프라인으로 모든 스윕 패널이 2026-09-23 에서 끊겨 있다(실측: panel_* 17개 전부 "
        "max date 2026-09-23). 원천은 이미 복구됐고(market_data 2026-11-10 · foreign_institutional/"
        "supply_market_features 2026-11-09) 신선 구간 30영업일이 아직 어떤 패널에도 없다. 따라서 "
        "'조정 축은 상한 +0.011, 새 레버는 데이터 축'이라는 19사이클 결론은 **신선 구간에서 검증된 적이 없다**. "
        "150일 창(end-date 고정)으로 다시 빌드하면 게이트 arm(CO_rank_smooth_d1_h5)과 코어 대조군"
        "(CO_core30_h5)을 같은 패널·같은 런에서 신선 구간 포함으로 채점할 수 있다."
    ),
    "metric": "wf_sweep_summary",
    "command": cmd,
    "counterfactual": "CO_core30_h5 (게이트 OFF 코어, 같은 런·같은 패널·같은 구간)",
    "success": (
        "① 패널 빌드 완료(panel_150d_fresh.npz, dates max = 2026-11-07~11-09 · 5폴드 성립) "
        "② 짝 Δ(arm−control) 5폴드×5시드 평균 ≥ +0.02 이고 폴드승률 ≥ 4/5 (폴드 std ±0.03 감안) "
        "③ 미달이면 '노이즈'로 명시하고 데이터 축 가설을 신선 구간에서도 기각 기록. "
        "rc≠0 이거나 요약 미갱신이면 '실행실패'(노이즈 아님)."
    ),
    "est_minutes": 300,
    "note": (
        "2026-11-10 21:2x 엔지니어 자율 신설(장외 루프). 근거 실측: ① XR33 셋업 검증 "
        "(`python3 scripts/_xr33_backfill_check.py` → verdict=complete): 공백창 2026-10-12~10-31 "
        "15/15 거래일 채워짐 · foreign_institutional 14,081행 · 비영 98.3% ② CG178 의 갭 창"
        "(10-09~11-06)도 20/20 거래일 완비(md·fi·smf·short) ③ 그런데 스윕 패널 17개 전부 "
        "max date 2026-09-23 → 신선 구간 미반영(실측 scripts/_panel_periods.py). "
        "즉 CG178 ③(게이트 arm 재측정)은 **패널이 없어 실행 불가**였고, 이 항목이 그 선행이다. "
        "설계: 150일 창 = 약 105거래일 × 49종목 ≈ 5,145 페어 → U3 실측 0.42 pair/s 면 빌드 ≈204분 "
        "+ 스윕 2 config×5폴드×5시드(50 cell) → est 300분, 컨테이너 timeout 은 구동기 요구식"
        "(est×60×1.5+300=27,300s)에 맞춤. --end-date 2026-11-09 로 고정해 체크포인트 재개가 성립. "
        "⚠ 대조 해석 한계: 이 패널은 281일 창(panel_420_asofpatch)과 **구간이 다르다** → 절대값을 "
        "기록 기준선 0.5406 과 비교하면 '타 패널 대조'로 증거가 약하다(U3 전철). 판정의 1차 근거는 "
        "**같은 런 안의 짝 Δ**이고, 절대값은 참고로만 병기하라. ⚠ 파이프라인 종료 후 틱이 ETA 가드를 "
        "통과하면 자동 착수(23:00 틱 → 종료 ~04:00 < 개장 09:00). 파이프라인과 동시 실행 금지."
    ),
    "created_at": now,
}

items.append(item)
d['updated_at'] = now
json.dump(d, open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
print('CG184 registered', item['est_minutes'], 'min')
import subprocess
print(subprocess.run(['python3', 'scripts/model_engineer_cycle.py', '--status'],
                     capture_output=True, text=True).stdout[:600])
