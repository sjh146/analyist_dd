#!/bin/bash
# 회사 다이제스트 (헌장 §7·§8) — 역할 보고를 하루 1회 한 장으로 모아 전달한다.
# Hermes 크론 `quant-company-digest` (매일 22:50, no_agent) 이 이 스크립트를 돌리고
# stdout 이 그대로 Discord 로 간다. CLI 로 보려면 data/reports/company_digest_last.txt 를 읽는다.
cd /home/jhshi/analyist_dd || exit 1
set -a; . /home/jhshi/analyist_dd/.env; set +a
export POSTGRES_HOST=127.0.0.1 POSTGRES_PORT="${POSTGRES_HOST_PORT:-5434}"
export PROJ_DIR=/home/jhshi/analyist_dd
out=$(/usr/bin/python3 scripts/company_digest.py 2>&1)
echo "$out"
printf '%s\n' "$out" > /home/jhshi/analyist_dd/data/reports/company_digest_last.txt
