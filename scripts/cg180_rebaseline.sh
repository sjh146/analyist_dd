#!/bin/sh
# CG180 재기준선 래퍼 — 챔피언 **단일 모델** 5폴드 robust AUC 재측정.
#
# 왜 래퍼가 필요한가(실측 2026-11-10 00:00 CG180):
#   감사 도구의 `--models` 는 **JSON** 을 요구하는데(`[["champ","/app/app/models/champion"]]`),
#   그 문자열이 `bash → docker exec → sh -c → python` 4단 셸을 지나면서 `[[` 가 셸 문법으로
#   해석돼 백슬래시가 남은 채 전달됐다 → json.decoder.JSONDecodeError(char 2) 로 rc=1 즉사.
#   인용을 겹겹이 쌓는 대신 **셸 파싱을 한 번만 거치는 파일**에 JSON 을 두면 재현이 안정적이다.
#
# 조건: `--robust-oos-out` 은 len(model_spec)==1 일 때만 기록된다(감사 도구 L583).
# 산출물: /app/reports/overnight/cg180_audit.json(폴드 상세·순기대) + cg180_rebaseline.json(게이트용 증거)
set -eu
cd /app
export OMP_NUM_THREADS=4
exec python -u scripts/model_metric_protocol_audit.py \
  --models '[["champ","/app/app/models/champion"]]' \
  --folds 5 --dates-per-fold 10 --stocks 80 --horizon 5 --label-kind rel \
  --out /app/reports/overnight/cg180_audit.json \
  --robust-oos-out /app/reports/overnight/cg180_rebaseline.json \
  "$@"
