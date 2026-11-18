구현 완료. 최종 보고입니다.

## 변경 파일 (신규 3개 + 스펙 §2가 명령한 스모크 산출물)

| 파일 | 내용 |
|---|---|
| `scripts/rebuild_panel.py` | 신규 — 스펙 §1 계약 전체 구현 |
| `tests/test_rebuild_panel.py` | 신규 — 스펙 §3 계약 5개 |
| `reports/panel_rebuild.json` | 신규 — 빌드 조건 리포트(스모크 실행 실측) |
| `services/xgboost-ml/app/models/wf/panel_smoke_rebuild.npz` (+`.sidecar.json`·`.build_meta.json`·`.lock`) | 스펙 §2(a) 검증 명령이 만든 스모크 산출물 |

**기존 파일 수정: 0건.** `wf_wave.py`·`panel_meta.py`·`dq_claim.py`는 import만, 기존 테스트 기대값 무수정, git 명령 미실행, DB는 SELECT + 자기신고 INSERT(`dq_runner_claim`)만.

## WHY + 실측 근거 (docstring에 조회 명령 포함)

- **패널 계측** `np.load(panel_prod200.npz, allow_pickle=True)` → X **(54800, 213)**, 종목 200, 날짜 279, 구간 **2025-08-04~2026-09-23**, mtime 2026-10-03 03:25 KST(=10-02 18:25 UTC 빌드), 전행 0 피처 **24**, nnz<5% **43**
- **생존 판정** `data/reports/feature_survival.json` → total 213 = alive **117** / dead **69**(CONST_ZERO 24 + SPARSE 45) / 조건부 27
- **원천 시계** (psql SELECT) `news_events` 2026-04-01~, `sns_post_features` **2026-09-23**~, `stock_sentiment` **2026-11-15**~ → 패널 구간 끝(09-23) 기준 SNS는 하루, 센티먼트는 0일 겹침
- **커버리지** `source_coverage_sweep.json` → 뉴스 27/200, SNS 29/200, 수급 91/200, 이벤트 191/200
- 결론: **패널 빌드 시점의 소스 공백**이 dead의 큰 원인 후보(H1). 스크립트는 `wf_wave.build_panel()`을 그대로 호출해(복제 금지) 새 파일에 재빌드하고 실측값을 sidecar/JSON으로 남긴다.

## 계약 구현 포인트

- **import만**: `_build()`가 `wf_wave.build_panel`을 지연 import 호출 (지연인 이유: wf_wave는 모듈 import 시 드라이버 로드 — 호스트 검증 경로는 표준 라이브러리만으로 돌아야 함)
- **exit 2**: `--panel-out` 누락(argparse)·basename ∈ {`panel_prod200.npz`, `panel_420.npz`}·`--end-date` 형식 오류. **exit 3**: 호스트 실행 시 traceback 없이 docker exec 명령 안내
- **sidecar**: `{panel}.sidecar.json` + `--json-out`에 스펙 스키마 전부(`codes_sha1`=정렬 고유 종목코드 sha1, 행순서 무관)
- **멱등**: sidecar/json은 원자적 덮어쓰기, 재실행 시 재사용 경로 + `reused:true` 플래그(조용한 재사용 숨기지 않음)
- **자기신고 3분리**: `dq_claim.record_claim` 재사용 — source=273(파이프라인 수신) / claimed=273(파서 생성) / **persisted=273(npy 디스크 재독해)** → `disk_ok=1`, 3행 기록됨(id 434·435·436)

## 검증 출력 (실제 실행)

**① pytest (호스트):**
```
tests/test_rebuild_panel.py::test_baseline_panel_path_rejected PASSED
tests/test_rebuild_panel.py::test_panel_out_required PASSED
tests/test_rebuild_panel.py::test_dry_run_does_not_call_build PASSED
tests/test_rebuild_panel.py::test_sidecar_report_keys PASSED
tests/test_rebuild_panel.py::test_end_date_format_rejected PASSED
============================== 5 passed in 0.36s ===============================
```

**② 스펙 §2(a) 스모크 (컨테이너):**
```
rebuild_panel 시작: /app/app/models/wf/panel_smoke_rebuild.npz (days=60 limit=8 end-date=2026-11-17 universe=prod seed=0)
universe[prod]: 8 종목 (limit=8)
빌드 구간: 2026-09-18 ~ 2026-11-17 (end_date 고정 — 체크포인트 재개용)
panel: (273, 205) features=213
panel cache 저장: /app/app/models/wf/panel_smoke_rebuild.npz
sidecar 저장: /app/app/models/wf/panel_smoke_rebuild.npz.sidecar.json
실제 저장 검증: npz X.shape[0]=273 (df rows=273, 일치=True)
완료: rows=273 dates=[2026-09-18,2026-11-17] n_features=213 codes=7 codes_sha1=125f8ae70f2d2627c7c0338560cca1b9c71616aa reused=False
```
후속 확인: `X.shape[0]==sidecar rows==273` ✓, sidecar dates == npz 구간 ✓ (호스트에서 직접 재검증).

**③ 스펙 §2(b) 생존 리포트 (호스트):**
```
/usr/bin/python3 scripts/feature_survival_report.py --panel services/xgboost-ml/app/models/wf/panel_smoke_rebuild.npz --json-out /tmp/fs_smoke.json --md-out /tmp/fs_smoke.md --quiet && echo OK
OK
```
(exit 0 — 스모크 패널 기준 alive 86/dead 49/조건부 78. 수치 해석은 오케스트레이터 담당)

## 준수 확인

- 기준선 불변: `panel_prod200.npz` mtime 2026-10-03 / `panel_420.npz` mtime 2026-09-25 그대로
- 컨테이너 재시작 없음, 실주문·브리지 코드 무접근, 리포 밖 파일 없음(/tmp 산출물만, 이후 docker cp로 리포 루트에 배치)

## 참고사항

1. **codes=7 vs limit=8**: prod 유니버스가 8종목을 골랐으나 1종목이 빌드 중 탈락(wf_wave 내부 동작). 리포트는 실제 결과만 기록.
2. `reports/panel_rebuild.json`은 **스모크 실행의 기록**(days=60·limit=8·rows=273). 본 재빌드(470일·200종목)는 오케스트레이터가 실행하면 이 파일이 같은 스키마로 갱신된다 — 기준선과 비교할 때 `codes_sha1`로 같은 종목 구성인지부터 대조하면 된다.
3. 중간에 sidecar가 mkstemp 0600(root 소유)이라 호스트에서 못 읽는 결함을 발견해 `_write_json`에 `os.chmod(0644)` 수정 후 재검증 완료(관련 메모 저장).
