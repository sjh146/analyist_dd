#!/usr/bin/env python3
"""2026-11-23 19:4x 백로그 위생 + 데이터 축 이중경로 등록 (엔지니어 자율 틱).

① pending 인데 command 가 없는 3건(CG145·CG191·CG361) → needs_setup 강등
   (구동기 자기모순 경고 3건/틱 제거, 실행 큐 정리 — 남은 일은 '코드 구현'이지 실행이 아니다)
② CG448 등록 자기모순 2건 수리: 컨테이너 timeout 11400→16500(필요값=180분×1.5+300),
   counterfactual 첫 토큰을 태그명(LS_quant_q30_h5)으로 (판정자 대조군 인식)
③ CG449 등록: 인트라데이 준비도 계측을 이중 경로(minute_bars / minute_close_bars)로 수리 — done
④ CG450 등록: Naver 전구간 분봉 기반 인트라데이 피처 A/B 사전등록 (needs_setup, not_before 2026-12-07)
⑤ CG451 등록: 패널 200종목 분봉 지속 적재(크론 변경 = 승인 대상)
"""
from __future__ import annotations

import json
import os

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKLOG = os.path.join(PROJ, "docs", "QUANT_MODEL_BACKLOG.json")
NOW = "2026-11-23T19:45:00+09:00"

CMD448_OLD_TIMEOUTS = [("timeout 11400 docker exec", "timeout 16500 docker exec"),
                       ("timeout 10800 python", "timeout 16200 python")]


def load():
    with open(BACKLOG, encoding="utf-8") as f:
        return json.load(f)


def save(b):
    tmp = BACKLOG + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(b, f, ensure_ascii=False, indent=2)
    os.replace(tmp, BACKLOG)


def main() -> int:
    b = load()
    items = b["items"]
    by = {i["id"]: i for i in items}
    log = []

    demote = {
        "CG145": ("앵커 평균 프로토콜 **구현**이 남았다(실행 실험이 아니다) — champion 판정 경로에 "
                  "복수 고정 앵커(--anchors d0,d2,d5) 평균±std 를 넣고 짝 Δ 문턱을 앵커 std 로 "
                  "재정의하는 코드 변경 + 회귀 테스트 1개. 승인은 2026-11-17/11-21 완료, "
                  "구현 후 command 에 앵커 6~8개 재측정 한 줄을 채워 pending 으로 올린다."),
        "CG191": ("선행: 실계좌 청산 기록 적재(현재 청산 0건) — 트레이더 경로 소유. 그 뒤 "
                  "robust_oos 표본 정의를 close_gate_probe(게이트 열린 날 0/113) 대신 journal 청산으로 "
                  "바꾸는 코드 변경이 필요하다(승인 완료). 실행 명령이 아니라 코드 작업이므로 needs_setup."),
        "CG361": ("스코어보드 기준선 이관은 **코드 변경 + 회귀 테스트**다(실행 런이 아니다): "
                  "quant_scoreboard 기준선을 22날짜 OOS 0.5132±0.0109 로 재등록 + 무개선 카운터 재계산 + "
                  "objective.json 에 프로토콜 명기. 선행 판단 근거는 CG448(11-23 21:07 착수) 결과 — "
                  "이관 후 후보 arm 도 같은 프로토콜로 병기해야 비교가 성립한다."),
    }
    for iid, setup in demote.items():
        it = by.get(iid)
        if not it or it.get("status") != "pending":
            log.append(f"skip {iid} (status={it.get('status') if it else 'absent'})")
            continue
        it["status"] = "needs_setup"
        it["setup_needed"] = setup
        it["command"] = None
        it["updated_at"] = NOW
        it["note"] = ((it.get("note") or "") +
                      " | 2026-11-23 19:4x 엔지니어: command 미정 pending 이 매 틱 자기모순 경고를 "
                      "띄우고 실행 큐를 점유 → needs_setup 강등(남은 일 = 코드 구현). "
                      "구현 완료 시 command·counterfactual·success 를 채워 pending 승격.")
        log.append(f"demote {iid} → needs_setup")

    it = by["CG448"]
    cmd = it["command"]
    for old, new in CMD448_OLD_TIMEOUTS:
        if old in cmd:
            cmd = cmd.replace(old, new)
    it["command"] = cmd
    it["counterfactual"] = ("LS_quant_q30_h5 (같은 런·같은 패널 panel_prod200_v6·같은 5폴드×3시드의 "
                            "등록 기준선 태그 그대로: 분위 q0.30 · top30 · 게이트 OFF · h5) — "
                            "차이는 arm 하나로 고정된다")
    it["est_minutes"] = 180
    it["updated_at"] = NOW
    it["note"] = ((it.get("note") or "") +
                  " | 2026-11-23 19:4x 등록 자기모순 2건 수리(엔지니어 자율): ① 컨테이너 timeout "
                  "11400/10800 → 16500/16200 (필요값 = est 180분×1.5+300s) ② counterfactual 첫 토큰을 "
                  "문장('같은')이 아니라 태그(LS_quant_q30_h5)로 — 판정자가 같은 런 대조군을 인식하지 "
                  "못해 '판정 불가' 경고가 났다. 명령·arm·문턱은 불변.")
    log.append("fix CG448 (timeout 16500/16200 · counterfactual tag)")

    if "CG449" not in by:
        items.append({
            "id": "CG449",
            "title": "[측정정합성] 인트라데이 준비도 판정이 minute_bars 한 테이블만 봤다 — 두 원천을 각각 채점하고 20일 도달 예상일을 낸다",
            "status": "done",
            "priority": 1,
            "owner": "engineer",
            "kind": "measurement_integrity",
            "affects_model": False,
            "metric": "data_axis_readiness",
            "command": ("python3 scripts/data_axis_readiness.py "
                        "--json data/reports/data_axis_readiness_20261123.json"),
            "counterfactual": "수리 전: minute_bars 단일 채점 → 'XR26 미수리'(봉 30)로만 보고, Naver 전구간 경로의 존재·이력을 못 봄",
            "success": "① 두 테이블(봉·이력·커버리지·구간·20일 도달 예상일)을 각각 출력 ② 틱 보고에 경로별 막는 숫자가 나온다 ③ 판정(ready)은 종전과 동일 기준(봉≥300 & 이력≥20 & 커버≥0.75)",
            "est_minutes": 3,
            "created_at": NOW,
            "updated_at": NOW,
            "result": {
                "verdict": "수리 완료 — 두 경로의 **막는 숫자가 다르다**를 실측으로 분리",
                "detail": ("minute_bars(KIS): 봉 30.0 · 이력 19일 · 패널커버 0.135 (2026-09-23~11-20) → "
                           "막는 것은 '봉'(XR26)  ·  "
                           "minute_close_bars(Naver): 봉 353.0 · 이력 9일 · 패널커버 0.18 "
                           "(2026-11-10~11-20, 347~426봉/종목·일) → 막는 것은 '이력'과 '커버리지'. "
                           "20일(사전문턱) 도달 추정 2026-12-07(평일 기준·공휴일 미반영). "
                           "즉 CG388(11-26) 은 봉 30 인 테이블로 판정하므로 '정보없음'이 나와도 "
                           "그것은 데이터 클래스 결론이 아니다 — 전구간 경로는 별도 판정이 필요하다."),
            },
            "evidence_file": "data/reports/data_axis_readiness_20261123.json",
            "note": ("2026-11-23 19:3x 엔지니어 자율(장외 틱·규칙 4). 코드: scripts/data_axis_readiness.py "
                     "check_intraday_table()/project_reach_date() + INTRADAY_TABLES 2경로, "
                     "scripts/model_engineer_cycle.py data_axis_readiness_note() 가 경로별 줄을 출력."),
        })
        log.append("add CG449 (done)")

    if "CG450" not in by:
        items.append({
            "id": "CG450",
            "title": "[데이터 축] Naver 전구간 분봉(minute_close_bars, 350봉/일)으로 만든 인트라데이 피처 A/B — 봉 30(꼬리)만 있는 KIS 경로와 분리 판정",
            "status": "needs_setup",
            "priority": 1,
            "owner": "engineer",
            "kind": "experiment",
            "affects_model": True,
            "metric": "wf_sweep_summary",
            "hypothesis": ("지금까지 인트라데이 판정 차량은 KIS minute_bars(종목·일당 30봉 = 15:01~15:30 꼬리)뿐이었고 "
                           "그 테이블로는 개장범위·장중 변동성 같은 피처 정의 자체가 불가능하다. 2026-11-23 실측으로 "
                           "Naver minute_close_bars 가 종목·일당 중앙 353봉(전 구간)을 담고 있으며 2026-11-10 부터 "
                           "적립 중임을 확인했다(이력 9일). 가설: 전 구간 종가·누적거래량만으로 만들 수 있는 피처"
                           "(① 시가 대비 종가 수익률 ② 오전/오후 수익률차 ③ 후반 30분 모멘텀 ④ 분봉 실현변동성 "
                           "⑤ 종가 위치(당일 고저 대비) ⑥ 누적거래량 기반 VWAP 괴리)는 다음날 방향(h1)에 "
                           "일봉 유도 피처가 못 담는 정보를 준다."),
            "arm": "CG450_intraday_h5",
            "counterfactual": "CG450_ctl_h5 (같은 런·같은 패널·같은 5폴드×5시드·게이트 OFF top30·인트라데이 열 미포함)",
            "command": ("docker exec stock_xgboost_ml sh -c 'cd /app && python -u scripts/patch_panel_intraday.py "
                        "--in app/models/wf/panel_prod200_v6o.npz --out app/models/wf/panel_prod200_idn.npz "
                        "--table minute_close_bars --report /app/reports/overnight/cg450_patch.json && "
                        "OMP_NUM_THREADS=4 timeout 5400 python -u scripts/wf_label_sweep.py "
                        "--panel /app/app/models/wf/panel_prod200_idn.npz --folds 5 --seeds 5 "
                        "--only CG450_ctl_h5,CG450_intraday_h5 --dump-preds /app/reports/overnight/cg450_both.jsonl "
                        "--summary-out /app/reports/overnight/cg450_summary.json "
                        "--out /app/reports/overnight/cg450_sweep.jsonl'"),
            "success": ("사전등록(변경 금지): ① 패치 리포트에서 인트라데이 열 커버리지(비영 비율) ≥ 0.5 "
                        "— 미달이면 측정무효(커버리지 낮은 열은 희석계수 때문에 선별에 못 들어간다) "
                        "② 짝 Δ(CG450_intraday_h5 − CG450_ctl_h5) 폴드 평균 ≥ +0.02 & 폴드 승률 ≥ 4/5 "
                        "③ 단일피처 |AUC−0.5| ≥ 0.044 인 인트라데이 피처 ≥ 1종 ④ 돈 지표는 승격 후보 단계에서만"
                        "(같은 런 덤프 + 체결성 필터 + 수수료 왕복 0.21%p). ② 미달이면 '전구간 분봉도 모델 레버 아님'으로 "
                        "데이터 축을 닫고 수집 확대를 중단한다."),
            "expected": ("미지. 단 h1 반전 신호(CG129: l30_ret IC −0.069 t −1.99)가 일봉 return_1d 와 부호가 "
                         "겹쳐 중복될 위험이 커, 정보있음 판정 시 증분 A/B(②)를 반드시 통과해야 한다."),
            "cost": "패치 ~10분(200종목·9~20일) + 학습 5폴드×5시드 2-arm ≈ 3~4시간",
            "est_minutes": 240,
            "not_before": "2026-12-07T20:00:00+09:00",
            "setup_needed": ("선행 2단: ① 이력 ≥20일 (현재 9일 · 2026-12-07 추정 도달 — data_axis_readiness 로 매 틱 확인) "
                             "② 패널 커버리지 ≥0.75 (현재 0.18 — 16:10 크론이 신호/저널 종목 50개만 받는다. CG451 참조). "
                             "③ 코드: patch_panel_intraday.py 에 --table 옵션 + 'close+cum_volume' 전용 피처 경로 추가"
                             "(현 스크립트는 minute_bars 의 open/high/low/trading_value 를 전제 — minute_close_bars 에는 없다). "
                             "④ wf_label_sweep 에 CG450_ctl_h5 / CG450_intraday_h5 두 config 등록(기본값 현행 유지)."),
            "created_at": NOW,
            "updated_at": NOW,
            "note": ("2026-11-23 19:4x 엔지니어 자율(장외 틱·pending 0건·규칙 4). 근거 실측: minute_close_bars "
                     "774,428행 · 452종목 · 9일(2026-11-10~11-20) · 중앙 353봉/종목·일(전 구간 381분의 93%). "
                     "⚠ 이 항목은 CG388/KIS 경로와 **별개 판정**이다(CG388 은 봉 30 테이블). "
                     "⚠ 승격은 이 루프에서 하지 않는다."),
        })
        log.append("add CG450 (needs_setup, not_before 2026-12-07)")

    if "CG451" not in by:
        items.append({
            "id": "CG451",
            "title": "[수집·승인] 패널 200종목 분봉을 매일 적재한다 — 16:10 백필 codes 목록에 패널 유니버스 합집합(현재 50종목 → 200종목)",
            "status": "needs_setup",
            "priority": 1,
            "owner": "engineer",
            "kind": "data_collection",
            "affects_model": True,
            "metric": None,
            "hypothesis": ("분봉 이력이 쌓여도 대상 종목이 feed/journal 50종목뿐이면 패널 커버리지 0.18 로 "
                           "사전문턱(0.75)을 영원히 못 넘는다 → 20일이 차도 실험 불가. 대상 목록을 "
                           "panel_prod200_v6o 200종목 합집합으로 넓혀야 한다(콜 수 4배: 50→200/일, "
                           "Naver 분봉은 2026-11-17 426종목 수집 전례가 있어 한도 내)."),
            "counterfactual": "현행 16:10 크론: minute_cover_codes.py(피드 후보 + 저널) → 47~50종목",
            "command": None,
            "success": ("① 16:10 크론 1회 실행에서 codes≥195 · rows_written≥250k(196종목×5일×300봉) "
                        "② data_axis_readiness 의 minute_close_bars panel_cover_frac ≥ 0.75 ③ 20일 도달 시 CG450 착수 가능"),
            "est_minutes": 15,
            "setup_needed": ("승인 시(크론 파일 수정 = 스킬 ②): `minute_cover_codes.py` 에 "
                             "`--include-panel` 옵션 추가(패널 npz codes ∪ 피드/저널, 기본값 현행 유지) 후 "
                             "사용자 crontab 16:10 줄에 `--include-panel` 한 토큰 추가. 되돌리기: 토큰 제거."),
            "created_at": NOW,
            "updated_at": NOW,
            "approval_needed": "크론 파일 수정 + 타 역할 스크립트(minute_cover_codes.py) 옵션 추가",
            "note": ("2026-11-23 19:4x 엔지니어 자율 등록. 오늘 19:3x 에 **일회성 백필**은 자율로 실행했다"
                     "(2026-11-10~11-20 · 패널 200종목 · 기존 스크립트 --codes-file, 코드 변경 0 · 추가형 쓰기). "
                     "그러나 매일 갱신은 크론 입력이 바뀌어야 하므로 승인 대상으로 올린다."),
        })
        log.append("add CG451 (needs_setup, 승인 대상)")

    save(b)
    b2 = load()
    print("\n".join(log))
    print("items:", len(b2["items"]))
    from collections import Counter
    print(Counter(i.get("status") for i in b2["items"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
