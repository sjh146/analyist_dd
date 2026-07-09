// ===========================================
// Neo4j Graph Schema
// 주식 자동매매 시스템
// ===========================================

// === 제약 조건 ===
CREATE CONSTRAINT stock_code_unique IF NOT EXISTS FOR (s:Stock) REQUIRE s.code IS UNIQUE;
CREATE CONSTRAINT sector_name_unique IF NOT EXISTS FOR (s:Sector) REQUIRE s.name IS UNIQUE;
CREATE CONSTRAINT industry_name_unique IF NOT EXISTS FOR (i:Industry) REQUIRE i.name IS UNIQUE;
CREATE CONSTRAINT theme_name_unique IF NOT EXISTS FOR (t:Theme) REQUIRE t.name IS UNIQUE;
CREATE CONSTRAINT cycle_name_unique IF NOT EXISTS FOR (c:Cycle) REQUIRE c.name IS UNIQUE;
CREATE CONSTRAINT twingroup_name_unique IF NOT EXISTS FOR (t:TwinGroup) REQUIRE t.name IS UNIQUE;

// === 인덱스 ===
CREATE INDEX stock_market_idx IF NOT EXISTS FOR (s:Stock) ON (s.market);
CREATE INDEX stock_sector_idx IF NOT EXISTS FOR (s:Stock) ON (s.sector);
CREATE INDEX stock_market_cap_idx IF NOT EXISTS FOR (s:Stock) ON (s.market_cap);
CREATE INDEX rel_belongs_to_idx IF NOT EXISTS FOR ()-[r:BELONGS_TO]-() ON (r.weight);
CREATE INDEX rel_twin_of_idx IF NOT EXISTS FOR ()-[r:TWIN_OF]-() ON (r.correlation);
CREATE INDEX rel_follows_cycle_idx IF NOT EXISTS FOR ()-[r:FOLLOWS_CYCLE]-() ON (r.phase);

// === 샘플 데이터 (개발용) ===
// 실제 운영 시 application layer에서 대량 입력

// 섹터 생성
MERGE (s:Sector {name: '반도체', description: '반도체 및 반도체 장비'})
MERGE (s2:Sector {name: '자동차', description: '자동차 및 자동차 부품'})
MERGE (s3:Sector {name: '금융', description: '은행, 증권, 보험'})
MERGE (s4:Sector {name: '바이오', description: '제약, 바이오, 헬스케어'})
MERGE (s5:Sector {name: 'IT', description: '정보기술, 소프트웨어'})
MERGE (s6:Sector {name: '에너지', description: '에너지, 화학, 정유'})
MERGE (s7:Sector {name: '경기소비', description: '유통, 의류, 엔터테인먼트'})
MERGE (s8:Sector {name: '소재', description: '철강, 비철금속, 건설'});

// 테마 생성
MERGE (t1:Theme {name: 'AI/인공지능', description: '인공지능 관련 테마'})
MERGE (t2:Theme {name: '2차전지', description: '이차전지 관련 테마'})
MERGE (t3:Theme {name: '전기차', description: '전기차 관련 테마'})
MERGE (t4:Theme {name: '로봇', description: '로봇 관련 테마'})
MERGE (t5:Theme {name: '신재생에너지', description: '신재생에너지 관련 테마'})
MERGE (t6:Theme {name: '방산', description: '방위산업 관련 테마'})
MERGE (t7:Theme {name: '대북/통일', description: '대북 관련 테마'})
MERGE (t8:Theme {name: '원자력', description: '원자력 관련 테마'});

// 사이클 생성
MERGE (c1:Cycle {name: '반도체 사이클', period_months: 18, current_phase: 'recovery'})
MERGE (c2:Cycle {name: '금리 사이클', period_months: 36, current_phase: 'tightening'})
MERGE (c3:Cycle {name: '건설 사이클', period_months: 60, current_phase: 'downturn'})
MERGE (c4:Cycle {name: '해운 사이클', period_months: 84, current_phase: 'peak'});

// 섹터-테마 관계
MATCH (sec:Sector {name: '반도체'}), (th:Theme {name: 'AI/인공지능'})
MERGE (sec)-[:THEME_RELATED {relevance: 0.95}]->(th);

MATCH (sec:Sector {name: '자동차'}), (th:Theme {name: '전기차'})
MERGE (sec)-[:THEME_RELATED {relevance: 0.9}]->(th);

MATCH (sec:Sector {name: '에너지'}), (th:Theme {name: '신재생에너지'})
MERGE (sec)-[:THEME_RELATED {relevance: 0.85}]->(th);

// 사이클-섹터 관계
MATCH (c:Cycle {name: '반도체 사이클'}), (sec:Sector {name: '반도체'})
MERGE (c)-[:AFFECTS {strength: 0.9}]->(sec);

MATCH (c:Cycle {name: '금리 사이클'}), (sec:Sector {name: '금융'})
MERGE (c)-[:AFFECTS {strength: 0.85}]->(sec);

// ===========================================
// Feature Engineering Pipeline 확장 (2026-07)
// ===========================================

// === 공시 노드 ===
CREATE CONSTRAINT disclosure_id_unique IF NOT EXISTS FOR (d:Disclosure) REQUIRE d.disclosure_id IS UNIQUE;

// === 거시경제 지표 노드 ===
CREATE CONSTRAINT macroindicator_name_unique IF NOT EXISTS FOR (m:MacroIndicator) REQUIRE m.name IS UNIQUE;

// === 샘플 공시 데이터 ===
MERGE (d1:Disclosure {disclosure_id: 'D20240331-001', title: '삼성전자 2024년 1분기 보고서', report_type: '분기', filing_date: date('2024-03-31'), summary: '1분기 매출 71.2조, 영업이익 6.6조'});
MERGE (d2:Disclosure {disclosure_id: 'D20240814-001', title: '삼성전자 2024년 반기보고서', report_type: '반기', filing_date: date('2024-08-14'), summary: '반기 매출 145.2조, 영업이익 16.6조'});

// === 샘플 거시경제 지표 ===
MERGE (m1:MacroIndicator {name: '기준금리', latest_value: 3.50, unit: 'percent', updated_at: date('2024-07-01')});
MERGE (m2:MacroIndicator {name: 'USD/KRW 환율', latest_value: 1380.50, unit: 'KRW', updated_at: date('2024-07-01')});
MERGE (m3:MacroIndicator {name: 'WTI 유가', latest_value: 82.30, unit: 'USD/barrel', updated_at: date('2024-07-01')});

// === 공시-종목 관계 ===
MATCH (s:Stock {code: '005930'}), (d:Disclosure {disclosure_id: 'D20240331-001'})
MERGE (s)-[:FILED {filing_date: date('2024-03-31')}]->(d);

MATCH (s:Stock {code: '005930'}), (d:Disclosure {disclosure_id: 'D20240814-001'})
MERGE (s)-[:FILED {filing_date: date('2024-08-14')}]->(d);

// === 공시-섹터 관계 ===
MATCH (d:Disclosure {disclosure_id: 'D20240331-001'}), (sec:Sector {name: '반도체'})
MERGE (d)-[:RELATES_TO]->(sec);
