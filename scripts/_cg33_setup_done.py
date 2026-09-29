#!/usr/bin/env python3
"""CG33 셋업 상태 갱신 — 스크래치 학습 경로 확인 완료(2026-09-29).

CG33 은 '챌린저를 챔피언과 같은 다중창 프로토콜로 평가'인데 setup_needed 가
'스크래치 학습 경로 미확인'이었다. 이번 틱에서 확인: retrain_champion 에 --out-dir(기존)·
--end-date/--start-date(신설) 가 있고 meta 에 data_start/data_end 를 기록한다 →
scratch 디렉터리 학습 + 같은 프로토콜 평가가 실제로 가능하다.
단 '더 나은 챌린저 설정'은 아직 없다(모든 arm 노이즈) → CG35(프로토콜 검증) 뒤로 순서를 미룬다.
"""
import json

P = "/home/jhshi/analyist_dd/docs/QUANT_MODEL_BACKLOG.json"
b = json.load(open(P))
it = next(i for i in b["items"] if i["id"] == "CG33")

it["status"] = "backlog"
it["depends_on"] = ["CG35"]
it["setup_done"] = (
    "2026-09-29 확인: retrain_champion.py 에 --out-dir(기존)·--end-date/--start-date(신설) + "
    "meta data_start/data_end 기록. champion_robust_eval.py 에 --train-start/--train-end 신설"
    "(겹치는 창 자동 제외, 테스트 19/19 PASS). 구동기 summary_path 도 커맨드의 --out 을 존중"
    "(9/9 PASS) → scratch 산출물을 같은 프로토콜로 평가하는 경로가 실제로 존재한다."
)
it["setup_needed"] = (
    "남은 것: ① CG35 로 '고정 컷오프 + 엄격 OOS' 프로토콜을 먼저 검증(챔피언 0.5883 이 암기인지) "
    "② 그 위에서 챌린저 설정을 정의 — 현재 배포 가능한 arm 중 프로토콜을 통과한 것이 없다"
    "(모든 arm 노이즈: CG26 k축·CG27 스무딩+depth1 +0.0144·CG28/29 데이터 축 +0.0005·CG32 정규화 악화). "
    "즉 챌린저가 정의되기 전에는 이 항목을 pending 으로 올리지 말 것."
)
it["command"] = (
    "docker exec stock_xgboost_ml sh -c 'cd /app && OMP_NUM_THREADS=4 timeout 5400 python "
    "scripts/champion_robust_eval.py --model-dir /app/app/models/scratch_challenger "
    "--folds 5 --dates-per-fold 10 --stocks 80 --train-start <학습시작일> --train-end <학습종료일> "
    "--out /app/reports/challenger_robust_eval.json'   # <학습시작일>·<학습종료일> 은 scratch 학습 meta 의 data_start/data_end"
)
it["note"] = (it.get("note", "") + " | 2026-09-29: CG34 로 프로토콜 결함(학습구간 겹침)이 실측됐고 "
              "코드로 차단됐다 — 챌린저 평가는 반드시 --train-start/--train-end 를 명시하라.")[:2000]

json.dump(b, open(P, "w"), ensure_ascii=False, indent=2)
print("CG33 updated:", it["status"], it["depends_on"])
