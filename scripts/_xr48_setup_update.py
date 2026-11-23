#!/usr/bin/env python3
"""XR48 셋업 구현 결과를 백로그에 반영 — sampler/판정 스크립트·재현 명령·사전등록 문턱을 채운다."""
import json
import os
from datetime import datetime

P = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "QUANT_MODEL_BACKLOG.json")
NOW = datetime.now().astimezone().isoformat(timespec="seconds")

b = json.load(open(P, encoding="utf-8"))
items = b["items"] if isinstance(b, dict) else b
by = {it["id"]: it for it in items}
it = by["XR48"]

it["status"] = "pending"
it["not_before"] = "2026-11-24T08:50:00+09:00"
it["est_minutes"] = 1
it["command"] = (
    "docker exec -e TZ=Asia/Seoul stock_xgboost_ml python3 -u "
    "scripts/r48_publish_window_report.py --jsonl /app/reports/overnight/r48_publish_probe.jsonl"
)
it["success"] = (
    "사전 등록(변경 금지): probe JSONL 의 관심세션(마지막 target_session)에서 최초 가용(두 마켓 ≥500행) "
    "시각 ≤ 07:30 → verdict '07:30_로더_가능' = 가설 채택(그때 07:30 T-1 로더 추가는 수집기·크론 변경이라 "
    "사용자 승인 항목으로 올린다). 최초 가용 > 07:30 또는 07:30 이후까지 미발행 → '현행 18:55 구조가 최선' "
    "으로 축 종결(로더 변경 없음). 표본부족(rc=3)은 판정 불가 — 재수집."
)
it["counterfactual"] = "현행 18:55 krx_daily 로더(T-2 종가로 08:40 아침 피드 스크리닝)"
it["metric"] = "krx_t1_publish_time"
base = (it.get("note") or "").strip()
add = (
    f" | {NOW[:16]} 엔지니어 셋업 구현(장외 틱·규칙 4): "
    "① 표본 수집기 scripts/r48_publish_sampler.py — krx_daily.fetch_day 재사용(net_guard 간격·차단 시 즉시 중단), "
    "15분 간격·23:13~11-24 08:40, 호출 2회/15분(마켓 1콜씩)이라 CPU·IP 부담 없음 → 컨테이너 내 detached(pid 118)로 "
    "기동. DB 쓰기 0. ② 판정기 scripts/r48_publish_window_report.py — 자정을 넘기면 target_session 이 바뀌므로 "
    "'마지막 target_session' 행만 판정에 쓰도록 구현(안 그러면 23:12 probe 가 '이미 발행'으로 잡혀 오독). "
    "실측 23:12: target=2026-11-20 → KOSPI 935·KOSDAQ 1,836행(당일 11-23 은 여전히 미발행, 리서처 관측 재현). "
    "산출 /app/reports/overnight/r48_publish_probe.jsonl(호스트 services/xgboost-ml/reports/overnight/)."
)
it["note"] = (base + add) if base else add
it["updated_at"] = NOW
b["updated_at"] = NOW

json.dump(b, open(P, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
open(P, "a").write("\n")
print("saved", P, "| XR48 ->", it["status"], it["not_before"])
