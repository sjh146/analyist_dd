# 테제원장 (Thesis Ledger) 계획서

> 작성일: 2026-08-27
> 목적: 빌 애크먼(Bill Ackman)의 투자 철학 — "세상에서 가장 좋은 사업을, 가장 좋은 경영진이,
> 본질가치보다 싸게, 촉매와 함께 산다" — 을 analyist_dd에 정량 모델로 이식한다.
> 핵심 장치: **매수 시점 테제를 냉동(frozen)하고, 매일 데이터로 검증하며, "파기" 판정 시에만 매도**하는
> append-only 원장(ledger). 기존 단기 전략(테마/사이클/쌍둥이/팩터)과 별도 슬리브로 병행 운영한다.

---

## 1. 개념 요약

### 1.1 에크먼 철학 → 시스템 매핑

| 철학 | 시스템 구현 |
|---|---|
| 복리 기계 (simple·predictable·FCF·dominant) | 퀄리티 스코어 (재무 데이터) |
| 본질가치 vs 가격 (마진 오브 세이프티) | 밸류에이션 스코어 (DCF/FCF yield z-score) |
| 촉매 필수 | 촉매 스코어 (`news_event_extraction.event_type` 기반) |
| 영구손실 회피 | 하드 베토 필터 (부채/감사의견/지배구조 리스크) |
| **What would change my mind?** | **테제 원장 + 매일 판정 → '파기' 시 매도** ⭐ |

### 1.2 핵심 규칙 (논의 확정 사항)

1. **테제는 냉동** — 매수 시점의 테제(사유 + 반박증거)는 수정 불가. AI가 매일 "재작성"하지 않고
   **고정 기준 대비 "검증"** 만 수행한다. (AI 자기합리화 방지 — 주가 하락에 테제가 따라 변질되는 것 차단)
2. **원장은 append-only** — 판정 결과는 INSERT만, 과거 판정 수정/삭제 금지. 테제 변경이 필요하면
   분기 리뷰에서 "새 결정"으로 별도 기록 (기존 테제는 `status='exited'` 처리 후 새 테제 생성).
3. **매도는 '파기'일 때만** — 가격 스톱 없음. 수익이든 손실이든, 매수 사유가 소멸한 순간 매도.
4. **반자동 승인** — 시스템이 후보 + 스코어 + 테제 초안을 생성 → 사용자가 승인 → 원장 등록.
   이후 모니터링/판정은 전부 자동.

---

## 2. 아키텍처 개요

```
[기존 파이프라인]                                    [신규 — 테제원장]
collector → deepseek_analyzer → event_extraction → clusterer
                                        │
                                        ▼
                              thesis_verifier (신규) ①
                              : active 테제 로드 → 오늘 이벤트 대조
                              : DeepSeek 판정 → thesis_verdicts INSERT
                                        │ '파기' 발생 시
                                        ▼ Redis pub/sub
                              AckmanStrategy (신규) ②  → 매도 시그널
                                        │
                              ackman_screener (신규) ③ → 매수 후보 + 테제 초안
                                        │ 사용자 승인
                                        ▼
                              position_theses INSERT (원장 등록)
```

- ① `services/news-analyzer/app/thesis/thesis_verifier.py` — 판정 엔진
- ② `services/strategy-agents/strategies/ackman_strategy.py` — 매수/매도 의사결정
- ③ `scripts/ackman_screener.py` — 후보 스크리너 (기존 `swing_screener.py` 패턴)

---

## 3. 스키마 설계 (`init-scripts/postgres/07_thesis_ledger.sql`)

```sql
-- === 포지션별 투자 테제 (원장 마스터) ===
CREATE TABLE IF NOT EXISTS position_theses (
    id SERIAL PRIMARY KEY,
    stock_code VARCHAR(10) NOT NULL REFERENCES stocks(stock_code),
    strategy_name VARCHAR(50) NOT NULL DEFAULT 'ackman_fundamental',
    thesis_text TEXT NOT NULL,            -- "왜 사는가" 2~3문장 (AI 초안 + 사용자 승인)
    disproof_criteria TEXT NOT NULL,      -- 반박증거: "이게 보이면 즉시 매도" ⭐
    intrinsic_value DECIMAL(20,4),        -- 본질가치 추정 (MoS = intrinsic/entry - 1)
    entry_price DECIMAL(20,4),            -- 진입가
    catalyst_events JSONB,                -- 기대 촉매 리스트 [{event_type, desc, deadline}]
    status VARCHAR(20) NOT NULL DEFAULT 'active',  -- active / exited / thesis_broken
    decision_log JSONB,                   -- 분기 리뷰/테제 변경 이력 (append-only)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- === 매일 판정 원장 (append-only, 수정 금지) ===
CREATE TABLE IF NOT EXISTS thesis_verdicts (
    id SERIAL PRIMARY KEY,
    thesis_id INT NOT NULL REFERENCES position_theses(id),
    verdict_date DATE NOT NULL,
    verdict VARCHAR(20) NOT NULL,         -- 강화 / 유지 / 약화 / 손상 / 파기
    verdict_score DECIMAL(5,4),           -- -1(파기) ~ +1(강화)
    evidence_event_ids INT[],             -- 근거 news_event_extraction.id 목록
    evidence_summary TEXT,                -- DeepSeek 판정 근거 1~2문장
    model_version VARCHAR(50),            -- 판정 프롬프트 버전
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(thesis_id, verdict_date)
);
CREATE INDEX IF NOT EXISTS idx_thesis_verdicts_thesis
    ON thesis_verdicts(thesis_id, verdict_date DESC);
```

- `positions` 테이블은 건드리지 않는다 (기존 전략과 호환 유지).
- `decision_log`에 분기 리뷰 기록: `[{ts, type: 'review'|'rewrite', old_id, new_id, reason}]`.

---

## 4. 판정 파이프라인 (`thesis_verifier.py`)

### 4.1 동작 흐름 (news-analyzer 30분 주기 파이프라인에 스텝 추가)

1. `position_theses WHERE status='active'` 로드.
2. 오늘 생성된 `news_event_extraction` 중 해당 종목 이벤트 조회 (없으면 판정 스킵 → 유지로 간주).
3. **이벤트 없을 때의 추가 입력**: `stock_sentiment` (감정 급변), `market_data` (급락/급등) — 테제와
   무관한 가격 변동은 판정에 영향 주지 않도록 프롬프트에서 명시.
4. DeepSeek 판정 프롬프트 (아래) → JSON 응답 파싱 → `thesis_verdicts` INSERT.
5. `verdict='파기'` → Redis pub/sub `thesis:break:{stock_code}` 발행 → AckmanStrategy 청산 트리거.

### 4.2 DeepSeek 판정 프롬프트 (초안)

```
당신은 빌 애크먼 스타일의 펀드 매니저입니다. 아래 "매수 테제"와 "오늘 새로 발생한 이벤트"를 대조해
테제가 여전히 유효한지 판정하세요.

[매수 테제]
{thesis_text}
[반박증거 — 이게 확인되면 테제는 파기]
{disproof_criteria}
[기대 촉매]
{catalyst_events}

[오늘의 이벤트]
{이벤트 목록: type, core_event_text, sentiment}

판정 규칙:
- 강화: 이벤트가 테제/촉매를 지지
- 유지: 관련 없음 또는 중립
- 약화: 일부 전제 흔들림 (일시적)
- 손상: 핵심 전제 손상 조짐 (추가 확인 필요)
- 파기: 반박증거 확인 또는 매수 사유 소멸
- 가격 변동만 있는 경우: 테제와 무관하면 반드시 '유지'

응답 JSON: {"verdict": "...", "score": -1.0~1.0, "evidence": [...], "summary": "근거 1~2문장"}
```

### 4.3 판정 5단계 ↔ 행동 매핑

| verdict | 의미 | 행동 |
|---|---|---|
| 강화 | 데이터가 테제 지지 | 홀드 (연속 강화 시 추가매수 후보) |
| 유지 | 변동 없음 | 홀드 |
| 약화 | 일부 전제 흔들림 | 워치 (판정 이력 모니터링) |
| 손상 | 핵심 전제 손상 조짐 | 매도 검토 (2회 연속 손상 → 파기 승격) |
| **파기** | 매수 사유 소멸 | **즉시 매도 시그널** |

---

## 5. 매수/매도 로직 (`AckmanStrategy`)

### 5.1 AckmanScore = Quality × Valuation × Catalyst (베토 필터 후)

| 축 | 구성 | 데이터 소스 |
|---|---|---|
| Quality | ROIC 5년 일관성, FCF 마진 안정성, 매출 CAGR, 이익 변동성(낮을수록), 부채비율(낮을수록) | yfinance 재무 + DART 재무제표 |
| Valuation | 정규화 FCF yield z-score (자기 과거 대비) + 업종 백분위 + 단순 DCF MoS | company_features 재사용 |
| Catalyst | 최근 6개월 이벤트 가중치: 자사주 매입/소각 > 밸류업 공시 > 배당 확대 > 지배구조 개선 > 스핀오프 | `news_event_extraction` |

**하드 베토** (1개라도 걸리면 제외): 부채비율 한도 초과 / 감사의견 비적정·한정 / 횡령·분식 공시 /
전환사채(CB) 대량 발행 / 최근 1년 거래정지 이력.

### 5.2 진입

1. `ackman_screener.py` → 유니버스(코스피+코스닥, 거래대금 필터) 대상 AckmanScore 산출 → 상위 10~15 후보.
2. 시스템이 후보별 **테제 초안 + 반박증거 초안** 생성 (DeepSeek, 재무+이벤트 데이터 입력).
3. 사용자 승인 시 `position_theses` INSERT → 분할 매수 실행 (기존 `trade-executor`/Creon 사용).
4. 포트폴리오 규칙: **동시 보유 5~10종목**, 종목당 10~20%, 리밸런싱 분기 1회.

### 5.3 청산 (가격 스톱 없음)

- `verdict='파기'` → 즉시 매도 시그널 (Redis → strategy-agents → trade-executor).
- `손상` 2회 연속 → 파기로 승격.
- 분기 리뷰: MoS 소멸(가격 ≥ intrinsic × 0.95) 시 '테제 달성' 처리 → 매도.
- 포지션별 최대 보유 2년 (기간 초과 시 분기 리뷰에서 재승인 필요).

---

## 6. 백테스트 설계

### 6.1 목표: "과거 10년간 이 모델이 살아남았나"

- 기존 backtester(`services/backtester`, `scripts/phase_4_backtest.py`)에 AckmanStrategy 등록.
- **촉매 재현**: 과거 DART 공시 데이터에서 자사주/배당/지분변동 이벤트를 추출해
  `news_event_extraction` 형태로 재현 → 판정 파이프라인을 과거에 "가상 실행".
- **판정 시뮬레이션**: 파기 조건(반박증거)을 정형 규칙으로 근사 — 예: 실적 2분기 연속 하향,
  배당 축소 공시, 점유율 관련 부정 뉴스 → `파기`.
- 성과 지표: CAGR, MDD, 승률, 평균 보유기간, **파기 판정의 정확도** (파기 후 6개월 수익률 분포).

### 6.2 기준선 비교

- 동기간 KOSPI 수익률, `quality_factor`(기존 팩터) 수익률과 비교 — 슬리브 가치 입증.

---

## 7. 구축 순서 (마일스톤)

| 단계 | 작업물 | 완료 기준 |
|---|---|---|
| M1 | `07_thesis_ledger.sql` + 테스트 | 마이그레이션 적용, 제약/인덱스 검증 |
| M2 | `thesis_verifier.py` + 판정 프롬프트 | 모의 테제+모의 이벤트로 5단계 판정 전부 정확 |
| M3 | `ackman_screener.py` (AckmanScore + 베토) | 과거 데이터로 스코어 분포 sanity check |
| M4 | `AckmanStrategy` + `ackman_fundamental` 등록 | `paper_only: true`, 백테스트 통과 |
| M5 | 촉매 재현 백테스트 (10년) | 결과 리포트 (`reports/ackman_backtest.json`) |
| M6 | 사용자 승인 플로우 + 분기 리뷰 CLI | paper trading 1분기 운영 |

---

## 8. 리스크 / 한계

- **AI 자기합리화**: 테제가 "현실에 맞게" 변질되는 것 — 냉동 원칙 + append-only로 방지.
- **정성 요소**: 비즈니스 이해/경영진 평가는 AI가 완전 대체 불가 → 반자동 승인 구조로 보완.
- **한국 시장 특성**: 행동주의(13D) 문화 부재 → 촉매 축은 밸류업/자사주/배당 중심으로 정규화.
- **데이터 갭**: 자사주 매입/소각 내역, 감사의견, 밸류업 공시 — DART OpenAPI 수집기 확장 필요
  (`dart_collector.py`에 공시 유형 추가).
- **판정 비용**: 포지션 10개 × 일 1회 DeepSeek 호출 — 무시 가능한 수준 (기존 감정 분석 대비 ~1%).

---

## 9. 추후 확장

- **테제 강화 자동 집행**: 연속 '강화' 판정 + MoS 유지 시 추가매수 자동화.
- **촉매 캘린더**: 기대 촉매의 데드라인 관리 → 기한 내 미발생 시 '약화' 가산점.
- **그래프 연동**: Neo4j 테마/사이클 노드와 테제 연결 → 섹터 차원 테제 (에크먼식 "섹터 베팅").
- **분기 리뷰 자동 보고서**: 원장 전체를 요약한 리뷰 문서 자동 생성 (Grafana 대시보드 추가).
