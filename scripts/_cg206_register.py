#!/usr/bin/env python3
"""2026-11-12 19:2x — 백로그 갱신: CG204 축 종결 표시 + CG206(인트라데이 재표본) 신설."""
import datetime
import json

P = '/home/jhshi/analyist_dd/docs/QUANT_MODEL_BACKLOG.json'
b = json.load(open(P))
items = b['items']
by = {i['id']: i for i in items}

c204 = by['CG204']
c204['status'] = 'closed_rejected'
c204['verdict'] = '축 종결 — CG204 arm 은 측정 이전에 구조적으로 무효였다'
c204['note'] = (c204.get('note', '') +
    " | 2026-11-12 19:1x 엔지니어 판정(축 종결): exclude_names 29개가 core48 후보풀과 교집합 0 인데 "
    "arm 은 core_only=True → 제외 대상이 후보에 없어 가드가 측정 전에 실패시켰다(로그: "
    "'exclude_names=[...29] 가 아무 컬럼도 제외하지 않았다'). 즉 이 arm 은 CO_smooth_d1_h5 와 같은 "
    "행렬이 되어 물어보려던 질문이 성립하지 않는다 — 게이트 ON 경로엔 종목상수가 후보로 들어오지 않는다"
    "(종목상수 21개 전부 core 밖, core 내 3개는 std=0). 같은 질문의 유효한 형태(게이트 OFF 경로)는 "
    "CG205 가 대신 측정했다: 짝 Δ -0.0099±0.0008(앵커 3/3 부호일관) 이고 날짜별 AUC 도 -0.0093 으로 "
    "pooled 와 같다 → 종목상수 9개는 횡단면 암기가 아니라 시점가변 AUC 에도 기여하는 정적 정보였다"
    "(누수 아님·게이트 OFF 과거 실험 오염 없음).")

if 'CG206' not in by:
    items.append({
        "id": "CG206",
        "title": ("[데이터 축·표본 갱신] 인트라데이(장마감 30분) 피처 스크린 재측정 — "
                  "분봉 표본이 4일→10일로 늘어 판정이 성립하는가"),
        "status": "pending", "priority": 1, "kind": "experiment",
        "owner": "engineer", "affects_model": False,
        "metric": "intraday_screen",
        "summary_out": "/app/reports/overnight/cg206_intraday_screen_resample.json",
        "command": ("docker exec stock_xgboost_ml sh -c 'cd /app && timeout 900 python -u "
                    "scripts/intraday_feature_screen.py --json-out "
                    "/app/reports/overnight/cg206_intraday_screen_resample.json'"),
        "counterfactual": "같은 행의 일봉 유도 피처(return_1d·gap·day_range) + 해시 기반 결정적 난수(null) 피처",
        "success": ("사전등록(변경 금지): 날짜별 IC |평균| >= 0.03 AND |t| >= 2 AND n_dates >= 20 "
                    "→ 정보있음. n_dates < 20 이면 판정불가로 적고(문턱 하향 금지) 표본 20일 도달 시 재측정."),
        "hypothesis": ("CG128 은 분봉이 6거래일·종목당 30봉뿐이라 n_dates 4 로 판정불가였다. 지금 "
                       "minute_bars 는 2026-09-23~11-11·91,290행(301종목·종목당 30봉)으로 완전일 10일까지 "
                       "늘었다 → '장마감 30분 매매압력'(l30_ret·l30_range·l30_vwap_dev·l30_realvol·"
                       "l30_slope·l30_vol_share 등 8종)이 일봉 대조군·난수 대조군보다 정보를 갖는지 잰다."),
        "evidence": ("2026-11-12 19:05 수동 사전실행(같은 명령): n_rows 3,000 · full_days 10 · h1 n_dates 9 · "
                     "h5 n_dates 8 → 최강 h1 l30_range IC +0.0788(t +1.62), h5 최강(비자명) l30_tv_share "
                     "+0.0355(t +0.51), 난수 대조 IC +0.0028(t +0.23). 즉 t 문턱 2 미달 + n_dates 20 미달 "
                     "= 판정불가. DB 실측(2026-11-12 19:0x): minute_bars 91,290행·301종목·종목당 정확히 "
                     "30봉(15:01~15:30) → XR26 결함이 오늘도 그대로(391봉 대비 13.0배 손실)."),
        "expected": ("미지 — 데이터 축(유일한 미측정 클래스)의 기대값 판정. 노이즈면 인트라데이는 "
                     "'커버리지 미확보+edge 미확인'으로 남고 XR26 패치의 기대값도 함께 내려간다."),
        "cost": "낮음 (DB 읽기 전용, 2분, 학습 없음)",
        "est_minutes": 5,
        "intraday_test": "feature_ic", "min_dates": 20, "min_delta_ic": 0.03, "min_t": 2.0,
        "created_at": datetime.datetime.now().astimezone().isoformat(timespec='seconds'),
        "note": ("2026-11-12 19:1x 신설(자율). 무개선 19사이클·조정 축 상한 +0.011 실측 이후 남은 축은 "
                 "데이터뿐인데 그 유일한 후보(인트라데이)가 CG128 에서 표본 부족으로 멈춰 있었다. "
                 "⚠ l30_ret/l30_range 는 당일 종가 방향과 기계적으로 겹치므로 h1 IC 를 look-ahead 로 오독하지 말 것."),
    })

b['updated_at'] = datetime.datetime.now().astimezone().isoformat(timespec='seconds')
with open(P, 'w') as f:
    json.dump(b, f, ensure_ascii=False, indent=1)
print('ok items=%d CG204=%s CG206=%s' % (len(items), by['CG204']['status'],
      by.get('CG206', {}).get('status')))
