#!/usr/bin/env bash
# XR26 검증 — 분봉 페이지네이션 패치를 적용해 회귀테스트를 돌리고 원복한다.
# 수집기 파일은 리서처 소유이므로 이 스크립트는 '검증'만 하고 흔적을 남기지 않는다.
set -euo pipefail
cd "$(dirname "$0")/.."
TARGET=services/kis-collector/kis_app/collectors/minute_collector.py
PATCH=data/reports/xr26_minute_pagination.patch

if git diff --quiet -- "$TARGET"; then
  APPLIED=0
else
  APPLIED=1
fi
revert() { [ "$APPLIED" = 0 ] && git checkout -- "$TARGET" || true; }
trap revert EXIT

if [ "$APPLIED" = 0 ]; then
  git apply -p1 "$PATCH"
fi
python3 scripts/_xr26_pagination_test.py
echo "XR26 검증 PASS — 실제 적용은 승인 후: git apply -p1 $PATCH"
