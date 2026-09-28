#!/usr/bin/env python3
"""U3 rc=1 기록의 원인 정정 + 재시도 상태 복구 (2026-09-29, 1회성).

왜: 09-29 02:29 원장 기록이 "측정값 없음 — 종료코드 1" 뿐이라, 354분(5.9시간) 빌드가
32,576/32,576(100%)을 채운 뒤 **피처 코드 변경 감지로 저장이 거부**되어 전량 소실된 사실이
안 보였다. 실제 원인은 01:48 KST 커밋 c4b431c(feature_pipeline.py·market_features.py 수정)다.
또한 이런 인프라·코드 편집 사고는 가설의 결과가 아니므로 status 를 failed → pending 으로 되돌린다
(구동기 execute() 도 이제 rc=1+코드변경을 재시도 대상으로 처리한다).
"""
import json
import os

PROJ = "/home/jhshi/analyist_dd"
LEDGER = os.path.join(PROJ, "data/reports/model_engineer_ledger.jsonl")
BACKLOG = os.path.join(PROJ, "docs/QUANT_MODEL_BACKLOG.json")

CAUSE = ("종료코드 1 — RuntimeError: 빌드 중 피처 코드 변경 감지"
         "(code_sig 1790334858.601 → 1790614188.127) — 혼합 패널 방지를 위해 저장하지 않음")
DETAIL = ("측정값 없음 — 코드 편집 사고(가설 결과 아님): 빌드가 32,576/32,576(99.5%→완료)까지 간 뒤 "
          "01:48 KST 커밋 c4b431c(feature_pipeline.py·market_features.py 수정)로 code_sig 가 바뀌어 "
          "저장이 거부됐다 → 354분 전량 소실, 체크포인트도 폐기. 재대책: "
          "①feature_pipeline 이 종목 배치마다 서명을 검사해 조기 중단(검증 48/162 페어에서 중단) "
          "②u3_launcher 프리플라이트가 피처 코드 120분 무편집일 때만 착수 "
          "③구동기 failure_cause 가 rc=1 로그 꼬리의 예외 줄을 원인으로 기록")

# ── 원장 정정 ──────────────────────────────────────────────────────────────
rows = []
with open(LEDGER, encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line:
            rows.append(json.loads(line))
fixed = 0
for r in rows:
    if r.get("id") == "U3" and r.get("rc") == 1 and r.get("ts", "").startswith("2026-09-29"):
        r["detail"] = DETAIL
        if isinstance(r.get("parsed"), dict):
            r["parsed"]["cause"] = CAUSE
            r["parsed"]["root_cause"] = "빌드 중 피처 코드 변경(01:48 커밋 c4b431c) → 혼합 패널 가드가 저장 거부"
            r["parsed"]["lost_progress"] = "32,576/32,576 pairs (99.5%→완료 지점), elapsed 354.4min"
        fixed += 1
if fixed:
    tmp = LEDGER + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, LEDGER)
print(f"원장 U3 rc=1 기록 정정: {fixed}건")

# ── 백로그 정정 ────────────────────────────────────────────────────────────
b = json.load(open(BACKLOG, encoding="utf-8"))
for it in b["items"]:
    if it.get("id") != "U3":
        continue
    if it.get("attempts"):
        it["attempts"][-1]["detail"] = DETAIL
        it["attempts"][-1]["cause"] = "빌드 중 피처 코드 변경 → 혼합 패널 가드가 저장 거부(전량 소실)"
    it["status"] = "pending"          # 인프라·코드 편집 사고 → 가설 결과 아님, 재시도 대상
    it["retry_note"] = ("2026-09-29 rc=1 소실 → 재시도 4/5 (코드 프리즈 후 재빌드 — u3_launcher "
                        "프리플라이트가 피처 코드 120분 무편집일 때만 착수)")
    it["note"] = (it.get("note") or "") + (
        " | 2026-09-29 원인 확정: 빌드 99.5%(32,576페어) 완료 후 01:48 커밋 c4b431c 로 code_sig 가 "
        "바뀌어 저장 거부 → 354분 전량 소실(체크포인트 폐기). 대책 3종: feature_pipeline 종목 배치별 "
        "조기 중단(실측 48/162 페어에서 중단, _code_churn_abort_test PASS), u3_launcher 프리플라이트"
        "(피처 코드 최신 mtime 120분 이내면 착수 안 함), 구동기 failure_cause rc=1 예외 줄 기록. "
        "→ 착수 전제: 다른 역할이 services/xgboost-ml/app 을 편집하지 않는 밤(20:35~08:55)이 필요하다.")
json.dump(b, open(BACKLOG, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("백로그 U3 → pending 복구 완료")
