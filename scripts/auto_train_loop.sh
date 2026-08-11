#!/bin/bash
# auto_train_loop.sh — ML 자동 재학습 (v2: ml_infinite_loop 위임)
# 이전 버전은 삭제된 train_v4.py를 참조해 실패 → 최신 학습 루프로 위임
set -euo pipefail
cd "$(dirname "$0")/.."
exec bash scripts/ml_infinite_loop.sh
