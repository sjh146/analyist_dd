#!/usr/bin/env python3
"""백로그 노트 추가 — XR26 패치 단일화 + CG129 진행 노트 (2026-10-09 04:0x 자율 틱)."""
import json
import pathlib
import datetime

P = pathlib.Path("/home/jhshi/analyist_dd/docs/QUANT_MODEL_BACKLOG.json")
d = json.loads(P.read_text(encoding="utf-8"))
items = d if isinstance(d, list) else d.get("items") or d.get("backlog")

XR26 = (" | 2026-10-09 04:0x 엔지니어 자율(장외 루프): **패치 단일화** — 같은 파일을 고치는 diff 가 2개"
        "(10-08 `..._fix.patch` · 10-09 `..._pagination.patch`) 동시에 존재해 승인자가 어느 것을 적용해야 하는지"
        " 알 수 없는 상태였다(적용 실수 시 30봉 조기종료가 남거나 전 구간을 못 받는다). 최신 종료 로직(개장 도달 = 종료 ·"
        " 진행 없음 = 무한루프 방어) 기준으로 MAX_PAGES=20 · FID_CNT_DEFAULT=30(실측 상한 문서화) · docstring 을 합쳐"
        " `data/reports/xr26_minute_pagination.patch` **하나로 단일화**했고, 구 패치는 `.superseded` 로 개명(내용 보존)."
        " 검증(오프라인·KIS 호출 0): `bash scripts/_xr26_verify.sh` **12/12** · `python3 scripts/_xr26_fix_verify.py` **7/7**"
        " — 두 검증기가 같은 패치 하나를 통과. 실측: 현행 기본경로 30봉(15:01~15:30)·1콜 → 패치 후 391봉(09:00~15:30)·14콜,"
        " 57행/페이지 비정형에서도 391봉. 적용 지침 `data/reports/xr26_README.md`. 남은 것: 수집기 소유자 승인·적용 +"
        " 마감 후 재수집(호출 4,200/일 → net_guard 간격 정책).")

CG129 = (" | 2026-10-09 04:0x 엔지니어: XR26 패치 단일화 완료(권위 패치 1개 · 12/12+7/7 PASS · `data/reports/xr26_README.md`)"
         " → 이 항목의 선행은 여전히 '수집기 소유자 승인·적용 + 재수집'이다. 승인 즉시 나는 인트라데이 피처 빌더"
         "(feature_engine/intraday_features.py)와 청정 패널 패치로 스크린 재측정에 착수한다.")

hit = []
for i in items:
    if i.get("id") == "XR26" and i.get("status") == "needs_setup":
        i["note"] = (i.get("note") or "") + XR26
        hit.append("XR26")
    elif i.get("id") == "CG129" and i.get("status") == "needs_setup":
        i["note"] = (i.get("note") or "") + CG129
        hit.append("CG129")

now = datetime.datetime.now().astimezone().replace(microsecond=0).isoformat()
if isinstance(d, dict) and "updated_at" in d:
    d["updated_at"] = now
P.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
print("note 추가:", hit, "· updated_at", now)
