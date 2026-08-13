#!/bin/bash
# job-runner 진입점
# 1) swing_screener.py의 os.chdir(../services/xgboost-ml) 해석용 심링크 (기존 compose 패턴과 동일)
# 2) 리포트 디렉토리 보장
set -e
mkdir -p /opt/services
ln -sfn /opt/xgboost-ml /opt/services/xgboost-ml
mkdir -p /app/reports /app/factor_reports
exec uvicorn app.main:app --host 0.0.0.0 --port 8010
