# 엔지니어 승인 패킷 — 2026-10-08 16:0x (자율 틱)

## 상황 (실측)
- 백로그: pending **0** · 실행 중 사이클 **0** · 원장 미보고 **0** · 미전달 실행 **0**(6h 창)
- needs_setup **9건** — 전부 ①타 역할 소유 파일 또는 ②리뷰보드 승인에 막혀 있다.
- 모델측 축은 전부 실측 종결(변환·HP·앙상블·선별·가중·목적함수·라벨·유니버스·창·정규화·국면·중복열),
  돈 축도 널 기준선으로 종결(CG96~CG100·CG103). 남은 레버는 **데이터 축 = 수집뿐**.
- 따라서 이 틱의 산출물은 '새 실험'이 아니라 **승인 즉시 실행 가능 상태로 만든 목록**이다.
  → 억지 pending 승격은 같은 표본 재측정(축 재시험)이 되므로 하지 않았다.

## 승인 배치 권장 (한 번에 묶어 승인 권장)
권장 순서 = 효과/비용 순. 1·2·3 은 이 역할이 즉시 실행 가능(코드·검증 완료).

### 1. XR26 — 분봉 페이지네이션 조기종료 수리 (수집기, 1줄)
- 무엇: `services/kis-collector/kis_app/collectors/minute_collector.py:90`
  종료조건이 `len(page_bars) < fid_cnt(100)` 인데 KIS 1회 상한이 30봉 → 매일 1페이지에서 중단.
- 왜(실측): `minute_bars` = 거래일당 9,000행 = 300종목 × **30봉(15:01~15:30)** · 8거래일.
  수리 시 기본 경로가 **391봉(09:00~15:30)** · 호출 14회, 57행/페이지에서도 전 구간(7/7 PASS).
- 안 하면: 인트라데이 축(CG101·CG129)이 영구히 열리지 않는다 = 유일하게 남은 미지 데이터 영역 봉쇄.
- 승인하면(정확한 명령):
  ```
  cd /home/jhshi/analyist_dd
  git apply -p1 data/reports/xr26_minute_pagination_fix.patch
  python3 scripts/_xr26_fix_verify.py     # 7/7 PASS (2026-10-08 16:0x 재확인)
  ```
  주의: 호출량 종목당 1콜 → 약 14콜(KIS 당일분봉 API = 당일분만 제공).
  net_guard 간격 안에서 장 마감 후 분할 스케줄을 수집기 소유자가 함께 정해야 한다.
- 긴급도: 낮음(다음 개장 전이면 무방) — 단 **누적 기간이 필요하므로 빠를수록 좋다**.

### 2. CG131 — 스코어보드 기준선·헤드라인 arm 을 청정 패널(prod200)로 이관
- 무엇: `scripts/quant_scoreboard.py` 등록 기준선 0.5406·'대조가능 패널' 필터 변경.
- 왜(오늘 재확인 실측, 누수 지문 종목당 유니크값 중앙):
  | 컬럼 | panel_420_asofpatch(현 헤드라인) | panel_prod200(청정) |
  |---|---|---|
  | value_per | **1** | 118 |
  | value_pbr | **1** | 192 |
  | quality_roa | **1** | 3 |
  → 헤드라인 `TR_rank_h5 0.5519` 는 **누수 패널·비배포 arm(rank 변환 = 종목 단위 스트리밍 추론에서 재현 불가)** 값.
  CG130 실측(청정 패널): LS_quant_q30 0.5302 · CO_core30 0.5305 · CO_rank 0.5344 · TR_rank 0.5267
  → 청정 패널에선 최고 arm 도 대조군 대비 Δ+0.0042 = 노이즈.
- 안 하면: '19사이클 무개선' 경보의 인용 숫자·판정 근거가 누수 패널 위에 남는다(기준선·arm 동시 교체).
- 승인하면: 오프라인 증명(`data/reports/cg131_scoreboard_migration_probe_20261007.txt`,
  `cg131_migration_A/B.json`)대로 기준선 재등록 + '배포 가능' 조건 추가 + 헤드라인 패널 고정.
- 긴급도: 보통 — **판정 자체가 뒤집힐 수 있는 변경**이라 리뷰보드 결정 필요.

### 3. CG135 — objective.json acceptance 하한(min_robust_auc 0.5·min_live_signals 1) 강제
- 왜(실측): 두 키가 어떤 .py/.sh 도 읽지 않아 robust 0.4737 < 0.5 후보가 `would_promote`(CG133).
- 안 하면: 선언만 된 수용 기준이 계속 무시된다(측정 정합성).
- 승인하면: `services/xgboost-ml/app/training/champion_promote.py` 에 플래그 배선 완료(기본 OFF)
  → `scripts/objective.py::promote_flags` 2줄 추가 + `_promote_floor_gate_test.py` 11/11 PASS 재확인.
- 긴급도: 보통.

### 4~6. 타 역할 파일 (사양·증거 완료, 승인만 필요)
- **L5c** PIT 유니버스 교체 — `app/factors/universe.py:55` → `get_avg_trading_value_asof(asof_date=..)`
  (PIT 게터 `app/storage/postgres_storage.py:133` 이미 존재). 현행 유니버스가 전 날짜 고정 853종목 =
  미래정보 누수. strategy-agents 소유 → 승인 필요.
- **MT189** 단타(daytrading) 소비 배선 — `RunnerConfig.screeners=['close','swing']` 가 기본값
  (daytrading 포함)을 덮어 평가 0건. 피드는 정상(20건 발행). 트레이더 레포 = 실주문 경로 확장 승인.
  ⚠ R1 에 daytrading 키가 없어 문턱 없음(무제한) → screeners 개방과 **문턱 동반 추가** 권장.
- **FS1** 피처 스토어 초기 적재 + activation — 코드 계약·검증 완료(33/33). 운영 DB 대량 적재 +
  `use_feature_store=True` 배선 승인 필요(스키마 변경 없음).

### 종속 항목 (위 승인 후 자동 해소)
- CG129(인트라데이 스크린 재측정) ← XR26 + 인트라데이 구간을 덮는 패널 확장
- CG101(인트라데이 피처 4종 빌더) ← XR26 (피처 빌더·패치 도구는 이미 구현됨:
  `feature_engine/intraday_features.py` · `scripts/patch_panel_intraday.py` · 자체점검 존재)
- CG82(절대문턱 → 분위 top-k 소비 정책 + 보정 계층) ← 트레이더 파일 + 발행 계약 변경
- CG73(데이터 축 재개) ← 수집 범위 + 이력 누적(둘 다 리서처/수집기 소유)

## 사람이 직접 해야 하는 일
없음 (이 목록은 전부 승인만 받으면 이 역할/해당 역할이 실행한다).
