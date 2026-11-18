# 스펙 — 패널 재빌드 (죽은 피처 부활 1단계)

작성 2026-11-18. 사용자 지시: "죽은 피처는 왜 생긴 거지?" → 원인 4가지 확인 후 **1번(패널 재빌드)**부터 시작.
목표: **최신 소스 기준으로 같은 200종목 패널을 새 파일로 다시 만들어**, 죽은 피처 중 얼마가
"데이터가 없어서" 죽었는지 "빌더가 0으로 덮어서" 죽었는지 분리해 측정한다.

## 실측 진단(이미 확인, 재확인 불필요)

- 현행 패널 `services/xgboost-ml/app/models/wf/panel_prod200.npz` : 54,800행(200종목×279일), 2025-08-04~2026-09-23, 2026-10-02 빌드
- 피처 생존: alive 117 / dead 69 / 조건부 27 — dead 중 24개는 `null_ratio 0.0, std 0.0`(상수 0), 45개는 nnz<5%(희박)
- 원천 커버리지(패널 200종목 기준): 뉴스 27종목(패널 기간 내 4종목), SNS 29종목, 수급 92종목, 이벤트 191종목
- 원천 시계: `news_events` 2026-04-01~, `sns_post_features` 2026-09-23~, `stock_sentiment` 2026-11-15~
- 즉 **패널이 만들어진 시점의 소스 공백**이 dead의 큰 원인 후보다(가설 H1: "시계·커버리지 공백")

## 1. 신규 `scripts/rebuild_panel.py`

```
python3 scripts/rebuild_panel.py --panel-out PATH --days 470 --end-date YYYY-MM-DD
                                [--limit 200] [--universe prod] [--universe-seed 0]
                                [--dry-run] [--json-out reports/panel_rebuild.json]
```
- `scripts/wf_wave.py` 의 **`build_panel(cache, limit, days, end_date=..., universe=..., universe_seed=...)` 를 import 해서** 그대로 사용한다(빌드 로직 복제 금지 — 정의가 갈라지면 비교가 무의미).
- `--panel-out` 은 **필수**이며, 경로 basename 이 `panel_prod200.npz` 이거나 기존 기준선 패널
  (`panel_prod200.npz`, `panel_420.npz`)과 같으면 **거부(exit 2)** — 대조군 보존.
- 종료 후 `.sidecar.json`(패널 옆) 또는 `--json-out` 에 **빌드 조건**을 남긴다:
  `{days, end_date, universe, universe_seed, limit, rows, dates:[first,last], n_features, codes_sha1, panel_path, built_at}`.
- `--dry-run`: 빌드 없이 인자·경로·컨테이너 실행 명령만 출력.
- 컨테이너(`stock_xgboost_ml`, `/app` 마운트)에서 실행하는 것을 전제로 하되, **호스트에서 실행하면
  `docker exec` 명령을 안내하는 에러**로 끝낸다(파이썬 의존성 부재 시 traceback 금지).

## 2. 검증 (실제 실행)

```
# (a) 스모크: 작은 패널이 실제로 만들어지는지 (컨테이너)
docker exec stock_xgboost_ml python -u scripts/rebuild_panel.py \
  --panel-out /app/app/models/wf/panel_smoke_rebuild.npz --days 60 --limit 8 --end-date 2026-11-17
# (b) 스모크 패널로 생존 리포트가 도는지 (호스트)
/usr/bin/python3 scripts/feature_survival_report.py --panel services/xgboost-ml/app/models/wf/panel_smoke_rebuild.npz \
  --json-out /tmp/fs_smoke.json --md-out /tmp/fs_smoke.md --quiet && echo OK
```
- (a) 에서 npz 존재 + `X.shape[0]==행수` + sidecar 의 rows/dates 가 실제와 일치.
- (b) 는 exit 0 이면 충분(수치 해석은 오케스트레이터 담당).

## 3. 테스트 — `tests/test_rebuild_panel.py` (신규 5개)

① 기존 기준선 경로(`panel_prod200.npz`) 지정 → exit 2 ② `--panel-out` 누락 → exit 2
③ `--dry-run` 이 빌드를 호출하지 않는다(monkeypatch 로 build_panel 호출 감시) ④ sidecar 에
`codes_sha1`·`rows`·`dates` 키가 있다 ⑤ 인자 검증: `--end-date` 형식(YYYY-MM-DD)이 틀리면 exit 2

## 4. 금지

- **`panel_prod200.npz` 및 기존 패널 덮어쓰기 금지**(대조군 보존).
- `scripts/wf_wave.py` 등 **기존 스크립트 수정 금지**(import 만). 기존 테스트 기대값 수정 금지.
- 버전관리 명령 금지, 리포 밖 파일 수정 금지, 실주문·브리지·트레이더 코드 무접근.
- DB 쓰기 금지(SELECT 전용), 컨테이너 재시작 금지.
