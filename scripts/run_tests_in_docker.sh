#!/usr/bin/env bash
set -euo pipefail
# Run tests inside xgboost-ml container
docker compose run --rm xgboost-ml python -m pytest tests/ -v --tb=short
