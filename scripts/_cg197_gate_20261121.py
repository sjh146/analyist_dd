#!/usr/bin/env python3
"""CG197 를 pending → needs_setup 으로 내리고 command 의 셸 주석 접두를 제거한다.

왜(2026-11-21 실측):
- CG197.command 가 `# 선행: XR26 수리 후. 그 다음 한 줄 — docker exec ...` → 셸 주석이라
  실행해도 rc=0 no-op, 요약 JSON 미갱신 → 틱이 '실행실패'로 오판(가짜 실패 1건).
- 전제도 미충족: `panel_200liq_intraday.npz` 가 컨테이너에 **없다**(app/models/wf 에 intraday
  패널 0개). CG129 스크린 실측 n_dates=16 < 20 (사전문턱), data_axis_readiness 는
  intraday pairs=5143 · median_bars_per_day=30.0 · n_dates=19 · panel_cover_frac=0.135 → BLOCKED.
- 확정 판정 게이트는 이미 CG388(2026-11-26, 분봉 완전일 21일)로 등록돼 있다. 그 전에 CG197 을
  돌리면 같은 표본을 두 번 태운다.
"""
import json
from datetime import datetime, timezone, timedelta

PATH = "docs/QUANT_MODEL_BACKLOG.json"
KST = timezone(timedelta(hours=9))
bl = json.load(open(PATH))
items = bl["items"] if isinstance(bl, dict) else bl

CG197_CMD = (
    "docker exec stock_xgboost_ml sh -c 'cd /app && "
    "PYTHONPATH=/app python /app/scripts/patch_panel_intraday.py "
    "--in app/models/wf/panel_prod200_v6.npz --out app/models/wf/panel_prod200_id.npz "
    "--report /app/reports/overnight/panel_intraday_patch.json && "
    "OMP_NUM_THREADS=4 timeout 5700 python -u scripts/wf_label_sweep.py "
    "--panel /app/app/models/wf/panel_prod200_id.npz --folds 5 --seeds 5 "
    "--only CG197_ctl_h5,CG197_intraday_h5 "
    "--summary-out /app/reports/overnight/cg197_intraday_summary.json'"
)

SETUP = (
    "선행 3단: ① 분봉 커버리지 게이트 — n_dates>=20 이고 panel_cover_frac 가 의미 있는 수준"
    "(현 0.135)이 될 때까지 대기(CG388 이 2026-11-26 확정 판정을 맡는다). ② 통과 시 "
    "patch_panel_intraday.py 로 panel_prod200_v6.npz 에 인트라데이 열만 부착"
    "(panel_prod200_id.npz, 커버리지 리포트 필수). ③ 그 패널로 "
    "CG197_ctl_h5 vs CG197_intraday_h5 5폴드×5시드 한 런. 통과 판정 문턱은 기존 success 그대로."
)

changed = []
for it in items:
    if it.get("id") != "CG197":
        continue
    if it.get("status") != "pending":
        print("skip: status=", it.get("status"))
        break
    it["status"] = "needs_setup"
    it["command"] = CG197_CMD
    it["setup_needed"] = SETUP
    it["not_before"] = "2026-11-26T09:00:00+09:00"
    it["gated_by"] = "CG388"
    it["note_20261121"] = (
        "2026-11-21: command 가 셸 주석('#' 접두)이라 실행 시 rc=0 no-op 이었다 → 접두 제거 + "
        "needs_setup 강등(전제 패널 panel_200liq_intraday.npz 부재). CG129 스크린 n_dates=16 < 20, "
        "readiness cover_frac 0.135. 확정 판정은 CG388(11-26)."
    )
    changed.append(it["id"])

if changed:
    bl["updated"] = datetime.now(KST).isoformat(timespec="seconds")
    json.dump(bl, open(PATH, "w"), ensure_ascii=False, indent=2)
print("changed:", changed)
