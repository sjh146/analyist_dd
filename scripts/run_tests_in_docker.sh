#!/usr/bin/env bash
set -euo pipefail
# Run tests inside xgboost-ml container
# pytest is a dev dependency (requirements-dev.txt), not part of the runtime image.
docker compose run --rm xgboost-ml bash -c "\
  pip install -q -r requirements-dev.txt 2>/dev/null; \
  python -m pytest tests/ -v --tb=short"
