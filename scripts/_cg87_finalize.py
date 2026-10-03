#!/usr/bin/env python3
"""CG87 기록 정리 — (1) 원장 reported=True (2) 백로그 arm 필드를 config id 로 정정.

왜: ① 크론 세션이 결과를 직접 보고하면 reported 플래그를 직접 켜야 다음 정시 틱이 같은 결과를
중복 보고하지 않는다(스킬 규칙). ② 이 항목의 `arm` 을 산문으로 적어 구동기 judge 가
'[arm 미지정] ... 제외 최고' 폴백으로 CO_smooth_d1_h5 vs LS_quant_q30_h5 를 짝지었다 —
의도한 짝은 CO_core30_h5(게이트 ON 대조군) vs LS_quant_q30_h5(게이트 OFF) 였다.
원장은 감사 흔적이라 그대로 두고, 백로그 result 에 정정 쌍을 남긴다.
"""
import json
import sys

sys.path.insert(0, "/home/jhshi/analyist_dd/scripts")
import model_engineer_cycle as m  # noqa: E402

NOW = m.now_kst().isoformat(timespec="seconds")
led = m.load_ledger()
n = 0
for r in led:
    if r.get("id") == "CG87" and not r.get("reported"):
        r["reported"] = True
        r["reported_at"] = NOW
        n += 1
if n:
    m._rewrite_ledger(led)
print(f"ledger reported 표시 {n}건")

b = m.load_backlog()
it = next(i for i in b["items"] if i["id"] == "CG87")
it["arm"] = "CO_core30_h5"
it["counterfactual"] = "LS_quant_q30_h5"
res = it.get("result")
if isinstance(res, dict):
    res["paired_note"] = (
        "짝 정정: 게이트 ON 대조군 CO_core30_h5 0.5305 vs 게이트 OFF LS_quant_q30_h5 0.5302 "
        "→ Δ+0.0003 (청정 패널에서 게이트 비용 사실상 0; 누수판 −0.0051 과 대비). "
        "배포가능 arm CO_smooth_d1_h5 0.5355 vs CO_core30_h5 0.5305 → Δ+0.0050 (문턱 +0.02 미달 = 노이즈). "
        "기록 기준선 0.5406 대비 청정 참조치 0.5305 = Δ−0.0101 (<0.02 → 기준선 재등록 불요)."
    )
m.save_backlog(b)
d2 = json.load(open(m.BACKLOG, encoding="utf-8"))
print("CG87 result keys:", list((next(i for i in d2["items"] if i["id"] == "CG87").get("result") or {}).keys()))
