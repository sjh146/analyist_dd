#!/bin/sh
# CG186 재학습 → 같은 런·같은 창 OOS 채점 래퍼 (2026-11-10).
# WHY CG186(CG183 후속): CG183 은 --end-date 2026-11-09 로 학습해 fit_end=2026-10-19 가 되었고,
#   그 뒤 OOS 거래일이 12일뿐이라 3폴드x5일(최소 15일)을 만들 수 없었다(실측: part_b 즉시 반환).
#   즉 '신선 구간을 다 학습한 후보'는 구조적으로 다중폴드 판정이 불가능하다. 판정을 되살리려면
#   신선 구간을 남겨 두고 학습해야 한다 → end-date 2026-10-16(잔여 OOS 약 16거래일).
#
# 왜 래퍼인가 (실측 2026-11-10 00:0x CG180):
#   감사 도구 `--models` 는 JSON 인데 그 문자열이 bash → docker exec → sh -c → python 4단 셸을
#   지나면서 `[[` 가 셸 문법으로 해석돼 rc=1 즉사했다. 인용을 파일에 두면 셸 파싱을 한 번만 거친다.
#
# 왜 2단(재학습 + 채점)인가 (2026-11-10 19:0x 실측):
#   ① 종전 CG186 은 `metric: expectancy_pct_per_session` 이었는데 **그런 파서가 구동기에 없다**
#      (grep 0건) → 90분을 돌려도 판정이 '판정불가'로 남는다.
#   ② 재학습 후보는 데이터 창 끝(2026-11-09)까지 학습하므로, data_end 를 학습 종료로 읽는
#      _split_oos 가 **모든 평가창을 겹침으로 버려** OOS 가 0 이 된다 → 잴 수가 없다.
#   대책: retrain_champion 이 fit_end(마지막 적합일)·val_start(검증 첫날)를 기록하고,
#   evaluator 는 fit_end 를 우선한다(_meta_range). 이 래퍼는 val_start 이후만 폴드로 쪼개
#   **챔피언과 후보를 같은 창·같은 런에서 동시에** 채점한다(짝 Δ 가 성립).
#
# ⚠ val 은 조기종료(early stopping)에 쓰였다 — '완전 미사용'은 아니므로 보고에 병기한다.
# 산출물: /app/reports/overnight/cg186_audit.json (b_robust_auc.champ / .cand + c_net_expectancy)
set -eu
cd /app
export OMP_NUM_THREADS=4

OUT=/app/app/models/champion_cand_cg186
CKPT=/app/app/models/wf/ckpt_cg186

echo "[cg183] 1/2 재학습 시작 $(date '+%F %T') (라벨·피처 계약 = 배포 챔피언과 동일)"
python -u -m app.training.retrain_champion \
  --days 120 --stock-limit 200 --end-date 2026-10-16 \
  --label-kind h1_direction --drop-serving-blind-features \
  --out-dir "$OUT" --checkpoint-path "$CKPT"

echo "[cg183] 2/2 채점 준비 $(date '+%F %T')"
VS=$(python - <<'PY'
import glob, json, os
c = sorted(glob.glob("/app/app/models/champion_cand_cg186/training-result-*.json"),
           key=os.path.getmtime)
if not c:
    print(""); raise SystemExit(0)
m = json.load(open(c[-1]))
print(m.get("val_start") or "")
PY
)
if [ -z "$VS" ]; then
  echo "[cg183] val_start 없음 — 재학습 메타(training-result-*.json)에 fit_end/val_start 가 없으면" >&2
  echo "[cg183] 평가창을 자를 수 없다(전 창이 학습겹침으로 제외된다). 중단." >&2
  exit 2
fi
echo "[cg183] 후보 val_start=$VS → 이 날 이후만 OOS 폴드로 채점(챔피언 동시 채점)"

exec python -u scripts/model_metric_protocol_audit.py \
  --models '[["champ","/app/app/models/champion"],["cand","/app/app/models/champion_cand_cg186"]]' \
  --folds 3 --dates-per-fold 5 --window-start "$VS" --stocks 80 --horizon 5 --label-kind rel \
  --out /app/reports/overnight/cg186_audit.json \
  --robust-oos-out /app/reports/overnight/cg186_rebaseline.json
