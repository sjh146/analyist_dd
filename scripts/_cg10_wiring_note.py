#!/usr/bin/env python3
"""CG10 백로그 갱신(유니버스 배선 완료 + DB 실측) + QUANT_FINDINGS 기록 (2026-09-28 16:1x)."""
import json
import os
import shutil

PROJ = "/home/jhshi/analyist_dd"
p = os.path.join(PROJ, "docs/QUANT_MODEL_BACKLOG.json")
shutil.copy(p, p + ".bak-cg10")
b = json.load(open(p, encoding="utf-8"))

NEW_CMD = ("docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=2 timeout 43200 "
           "python -u scripts/wf_wave.py --days 420 --limit 200 --universe prod "
           "--panel /app/app/models/wf/panel_prod200.npz --end-date 2026-09-27'")

NOTE = (" | 2026-09-28 16:1x: **배선 완료(선행 조건 해소) + DB 실측**. "
        "`wf_wave.select_panel_codes`·`--universe prod|curated`·`--universe-seed`·`--universe-report`·"
        "`--panel` 신설, `wf_label_sweep` 에도 전달(기본값 현행 유지). DB 전용 검증(패널 빌드 없음): "
        "curated limit=200 → KOSDAQ 200·비주식 0 / prod limit=200 → KOSDAQ 141+KOSPI 59·비주식 0 / "
        "**교집합 19/200 = 9.5%**. 즉 지금까지 모든 스윕 Δ 는 챔피언 학습 유니버스와 90.5% 다른 "
        "종목 집합에서 측정됐다 — CG5(150종목 부호 반전)·CG11(전 성분 하회)의 유니버스 특이성과 "
        "정합하는 수치다. 남은 것은 패널 빌드뿐(200종목×~280일 ≈ 5.6만 페어 ≈ 15시간 · U3 와 같은 "
        "야간 슬롯 경쟁) → U3 완주 후 u3_launcher 방식의 명시적 런처로 착수. "
        "`--universe prod` 는 기본 파일명을 **거부**한다(기준선 패널 덮어쓰기 방지, rc=2 실측).")

for it in b["items"]:
    if it["id"] == "CG10":
        it["command"] = NEW_CMD
        it["est_minutes"] = 900
        it["note"] = (it.get("note") or "") + NOTE
        it["setup_needed"] = None
        it["status"] = "backlog"
        it["blocked_on"] = "U3 완주(야간 슬롯 1개) — U3 와 동시에 시작하면 4코어에서 둘 다 느려진다"
json.dump(b, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("CG10 updated:", json.dumps({k: v for k, v in
      next(i for i in b["items"] if i["id"] == "CG10").items()
      if k in ("status", "est_minutes", "command")}, ensure_ascii=False)[:600])

ENTRY = """
## [엔지니어] 스윕 유니버스와 프로덕션 챔피언 유니버스의 교집합은 **9.5%** — 26사이클 무개선의 구조적 원인 후보 (2026-09-28 16:1x)
- **실측(DB 전용, 패널 빌드 없음·`wf_wave.py --universe-report --limit 200`)**:
  curated 경로(`train_curated._select_universe`, 지금까지 모든 스윕) = **KOSDAQ 200종목·KOSPI 0**,
  prod 경로(`app.training.universe.select_training_universe`, 프로덕션 챔피언 학습기와 같은 함수)
  = **KOSDAQ 141 + KOSPI 59**, 비주식 0 → **교집합 19/200 = 9.5%**.
- **해석**: 지금까지의 모든 Δ 는 챔피언이 학습하는 종목 집합과 **90.5% 다른 종목**에서 측정됐다.
  CG5(같은 config 49종목 +0.0227 → 150종목 −0.0109 부호 반전)·CG11(panel_150u 에서 전 성분 하회)과
  정합한다 — 즉 '승격 관문에서 이득이 깎인다'가 아니라 **측정 표본 자체가 승격 조건을 대표하지
  않는다**. 이것이 무개선 26사이클의 구조적 원인 후보 1순위다.
- **배선(이 역할 소유 파일)**: `wf_wave.select_panel_codes` 신설 + `--universe prod|curated`·
  `--universe-seed`·`--universe-report`·`--panel` 옵션, `wf_label_sweep` 에 전달. 기본값은 현행
  유지(기준선 재현성 보존). `--universe prod` + 기본 패널 파일명은 **거부(rc=2)** — 기준선 패널
  덮어쓰기로 대조군이 사라지는 사고를 막는다(실측 확인).
- **다음**: CG10(panel_prod200.npz 200종목 빌드 ≈15시간) → CG9(생산 트레이너 옵션 + 후보 →
  champion_promote --dry-run). 두 항목 모두 승격 실행이 아니라 **판정만** 한다.
- **CG12 신설·즉시 실행**: 유니버스 크기 단조 추세(게이트 ON 대조군 25/49/75/100/150종목 + 우승
  config 4수준, panel_150u in-run A/B) — 등록 기준선 0.5406 이 소유니버스 낙관 편향인지 검정.
- **백로그 위생**: L5b·L5c(pending 인데 command 없음 → 매 틱 경고)를 needs_setup 으로 내리고
  승인 필요 사유를 명시(팩터/전략 코드는 이 역할 소유가 아님).
- **패널 진단(읽기 전용)**: panel_420_asofpatch 210컬럼 중 **전부 0 인 컬럼 10개**
  (credit_balance_change·days_to_cover·disclosure_count_5d·etf_flow_5d·institution_ownership_pct·
  margin_balance_change·sector_count·sector_momentum·short_interest_ratio·value_ncav).
  거시/시장레벨 컬럼 19개(cpi_yoy·fx_*·interest_rate*·oil_*·ppi_yoy·yield_spread·krx_* 등)는 값이 있다
  → XR11(거시 피처 부활)은 '패널에 없다'가 아니라 '횡단면 상수라 계약 #6상 그대로 투입 금지' 문제다.
"""

with open(os.path.join(PROJ, "docs/QUANT_FINDINGS.md"), "a", encoding="utf-8") as f:
    f.write(ENTRY)
print("QUANT_FINDINGS appended")
