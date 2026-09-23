# trader-agent 연동 계획 (analyist_dd → 종목 추출 → 매매)

목표 시스템:
API 수집(KRX/KIS/DART/뉴스) → 정제·피처 → ML 학습 → 종목 추출(스크리너 + ML 점수)
→ **피드 스냅샷 발행** → trader-agent 폴링 → 주문(크레온 브리지)

## 0. 배치 규칙 (경로 충돌 방지)

`trader-agent` 는 이 저장소 **안쪽에 두어도 되지만 별개 프로젝트**로 취급한다.

| 항목 | 규칙 | 이유 |
|---|---|---|
| git | 부모는 `/trader-agent/` 를 무시한다(.gitignore). 중첩 저장소를 `git add -A` 로 삼키지 않는다 | 서로 다른 origin·이력 |
| pytest | `norecursedirs = trader-agent` (pytest.ini) | 부모 `pytest` 가 남의 테스트를 수집하면 실패로 보인다 |
| docker | compose build context 는 `./services/*` 만 쓴다. 루트 컨텍스트 빌드 대비 `.dockerignore` 에 `trader-agent/` 포함 | 빌드 컨텍스트 비대화 |
| 스크립트 | 부모 스크립트는 `find` 를 로그 디렉터리 `-maxdepth 1` 로만 쓴다(재귀 정리 없음) | trader-agent 파일 삭제 사고 방지 |
| 실행 주체 | 매매(주문)는 **Windows** 의 trader-agent 가 한다 — 크레온 COM 브리지는 32비트 Windows 전용. WSL analyist_dd 는 데이터·ML·스냅샷 발행만 | 32비트 COM 제약 |

## 1. 실행 위치

| 구성요소 | 위치 | 비고 |
|---|---|---|
| 수집·ML·스크리너·피드 발행 | WSL `/home/jhshi/analyist_dd` (Docker 17컨테이너 + 호스트 크론) | Tailscale 노드 `dduckbeagy-wsl` = 100.93.220.52 |
| 피드 소비·주문·브리지 | Windows `C:\Users\jhshi\trader-agent` (loop 64비트 + bridge 32비트) | `bridge_start_bg.bat` → 127.0.0.1:8100 |
| 전달 | HTTP JSON (권장, Tailscale) 또는 공유 파일 | 아래 §3 |

## 2. 파이프라인 단계와 현재 상태

| 단계 | 담당 | 상태 |
|---|---|---|
| ① API 수집 | KRX OpenAPI 일봉(2콜/일) + KIS(분봉·구간백필) + DART 재무 + 뉴스/SNS | 일봉 1년 완료(247거래일, 공식 재수집), 분봉 300종목 23:00 크론, 재무 1,318/2,770 |
| ② 정제·피처 | xgboost-ml feature_engine (+ 거래정지 행 제외 필터) | 동작 |
| ③ ML 학습 | 챔피언 앙상블(xgb+lgbm+cat) + 캘리브레이션 | 동작(AUC 0.62 수준) |
| ④ 종목 추출 | `scripts/close_screener.py`, `swing_screener.py` (+ ML 점수) | `reports/close_latest.json`, `swing_latest.json` 생성 |
| ⑤ **피드 스냅샷 발행** | **미구현** — 계약 형식(`screener_latest.json`)으로 합쳐 서빙해야 함 | ← 여기부터 만들면 됨 |
| ⑥ 피드 소비·매매 | trader-agent `ScreenerFeedClient` → 게이트 1~6 → 주문 | 준비됨(계약 문서 존재), `feed_url` 이 옛 노드(100.127.186.48)로 남아 있음 |

## 3. 피드 계약 요약 (상세: trader-agent/FEED_CONTRACT.md)

```json
{
  "generated_at": "2026-09-23T19:30:00+09:00",   // 24h 넘으면 전부 차단(stale)
  "source": "analyist_dd",
  "candidates": {
    "close": {"items": [{"stock_code": "005930", "close_price": "278500", "score": "83.4"}]},
    "swing": {"items": [...]}
  },
  "scoring_summary": {"screener_stats": {"close": {"win_rate": 0.61, "avg_win_pct": 2.4, "avg_loss_pct": 1.3}}}
}
```

- 전략별로 **키를 섞지 않는다**(close/swing/daytrading 별도).
- `close_price` 는 **주문 지정가로 그대로** 쓰인다 → 신호 시점 가격을 넣는다.
- `score` = 보정 확률 × 100. **0~100에 퍼져 있어야** 문턱·상위 N 선별이 의미를 갖는다(뭉치면 무의미).
- 종가 전략은 **15:00 이전** 도착해야 그날 진입창(14:50–15:25)에 들어간다.
- `scoring_summary.screener_stats` 는 켈리 사이징 사전확률로 직결된다 — 피드 통계 > 자체 저널 > 기본값.

## 4. 남은 작업 (착수 순서 제안)

1. **피드 발행기**(analyist_dd): `close_latest.json` + `swing_latest.json` + ML 확률을 계약 형식으로 합쳐
   `data/feed/screener_latest.json` 생성(원자적 교체). 실행: 종가 15:00 전, 스윙 19:30 전후 크론.
2. **서빙**: 같은 파일을 HTTP 로 노출(Tailscale). 후보 — (a) api-gateway 정적 라우트 추가,
   (b) 기존 `news_gate_server` 처럼 경량 서버 systemd 유닛. 인증은 Tailscale 에 위임.
3. **trader-agent 설정**: `feed_url` 을 WSL 노드(`http://100.93.220.52:<port>/screener_latest.json`)로 교체.
4. **검증**: `run_trader_core.py scan --feed <url|파일> --dry-run` 으로 게이트 통과/차단 사유 확인 → 실거래 전 필수.
5. 뉴스 게이트(부정 이벤트 차단)를 피드 단계에 결합 — `scripts/news_gate_server.py` 가 이미 그 역할을 한다.

## 4. 진행 상태 (2026-09-23 구현분)

| 항목 | 상태 | 근거 |
|---|---|---|
| 피드 발행기 `scripts/feed_export.py` | **완료** | `reports/close_latest.json` + `swing_latest.json` → `data/feed/screener_latest.json` (원자적 교체, 계약 검증/점수분포 로그) |
| 피드 서버 `scripts/feed_server.py` + `analyist-feed.service` | **완료** | systemd 상시 구동(0.0.0.0:8090), `/screener_latest.json` + `/health`. Windows에서 `http://localhost:8090` 200(13.9KB, 0.22s), tailnet IP는 소형 응답만 통과(MTU 이슈 — §5) |
| 크론 | **완료** | 08:40 스윙 발행 / 14:40 종가 잡+발행 / 20:30 재발행 (`/etc/cron.d/analyist_dd`) |
| trader-agent 위치 | **완료** | `C:\Users\jhshi\analyist_dd\trader-agent`로 이동, .bat·예약작업·문서 경로 수정, 테스트 468개 통과 |
| trader-agent 피드 소비 | **완료** | `config.feed_url` = `http://127.0.0.1:8090/screener_latest.json`, `loop_start_bg.bat`도 `--feed <URL>` 로 교체 |
| 소비자 파서 검증 | **완료** | `tools/check_feed.py` → 파싱·신선도·가격누락·점수분포 확인(exit 0) |
| 매매 계획 검증(scan --dry-run) | **대기** | 브리지(크레온 로그인) 필요 — 사용자가 브리지 띄운 뒤 실행 |
| 켈리 사전확률 | **미포함** | 채점 이력이 쌓이면 `data/reports/screener_stats.json` 생산 → 자동 포함 |
| 당일 데이터 신선도 | **개선 필요** | KRX 공식 경로는 D-1까지만 채우므로 14:40 종가 피드의 가격은 전일 종가다. R3(±3% 이탈 스킵)가 방어하지만, KIS 당일 수집(유동성 상위 300종목, 18:55)을 추가하면 저녁 스윙 피드는 당일 종가를 쓸 수 있다 |

## 5. 남은 작업

0. **스윙 스코어 품질(우선)**: 2026-09-23 실측에서 스윙 잡이 `batch_type: raw_fallback` 으로 돌아
   confidence 가 0.5081~0.5391(20건 중 19건이 동일값 0.5081)에 몰렸다 — 챔피언 모델 배치가 연결되지
   않은 대체 경로다. 이 상태의 스윙 목록은 문턱·상위 N 선별이 사실상 무작위이므로 **실거래에 쓰면 안 된다**
   (발행기는 spread<5 경고만 남긴다). 종가 피드(score 83~90, spread 6.8)는 정상이다.
   → 조치: ML 배치/추론 경로를 연결해 confidence 분포를 퍼뜨린 뒤 스윙을 켠다.
1. **매매 계획 검증**: 크레온 로그인 → `bridge_start_bg.bat` → `run_trader_core.py scan --dry-run` 으로 게이트 통과/차단 확인.
2. **당일 종가 확보**(선택): KIS로 유동성 상위 300종목 당일 봉 수집(18:55, 약 16분) 크론 추가 → 저녁 스윙 피드가 당일 종가 사용.
3. **켈리 통계 연결**: `screener_score.py` 채점 이력이 쌓이면 `data/reports/screener_stats.json` 를 발행기가 자동 포함.
4. **tailnet 경로 MTU**: 원격 트레이더로 확장할 때 `100.93.220.52:8090` 대용량 응답이 멈춘다(소형 `/health`는 통과). SSH 터널 또는 MTU/MSS 조정 필요 — 같은 노트북에서는 `localhost:8090` 로 우회한다.
5. **낡은 산출물 가드**: 발행기는 스크리너 산출물이 3일을 넘기면 그 전략을 빈 리스트로 발행한다
   (`--max-source-age-days`). 계약이 보는 `generated_at`(발행 시각)만으로는 낡은 종목을 막을 수 없다.

## 6. 안전 규칙

- 실거래 전 반드시 dry-run 스캔으로 후보가 의도대로 통과하는지 확인한다(모의투자 계좌 → 실계좌 순서).
- `generated_at` 을 재사용하거나 과거 값을 보내면 전 후보가 조용히 차단된다 — 발행 시각을 매번 갱신한다.
- 점수 분포를 발행 로그에 남긴다(뭉침·이상치 조기 발견).
