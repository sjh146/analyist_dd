// ===========================================
// Neo4j News Intelligence Schema (Phase 7)
// 뉴스 이벤트 관계 예측
// ===========================================
// 기존 01_schema.cypher 에 additive. 기존 노드/관계 삭제 없음.
// 모든 구문은 MERGE + CREATE CONSTRAINT ... IF NOT EXISTS 로 멱등.

// === 제약 조건 ===
// Event 노드: event_id (cluster_key) 유일
CREATE CONSTRAINT event_id_unique IF NOT EXISTS FOR (e:Event) REQUIRE e.event_id IS UNIQUE;
// Theme 노드: name 유일 (01_schema.cypher 에 이미 존재하므로 IF NOT EXISTS 로 중복 방지)
CREATE CONSTRAINT theme_name_unique IF NOT EXISTS FOR (t:Theme) REQUIRE t.name IS UNIQUE;
// ImpactScore 노드: (stock_code, date) 유일
CREATE CONSTRAINT impact_score_unique IF NOT EXISTS FOR (i:ImpactScore) REQUIRE (i.stock_code, i.date) IS UNIQUE;

// === 인덱스 ===
CREATE INDEX event_type_idx IF NOT EXISTS FOR (e:Event) ON (e.type);
CREATE INDEX event_date_idx IF NOT EXISTS FOR (e:Event) ON (e.date);

// === 샘플/스키마 정의 (개발용) ===
// 실제 운영 시 application layer (news_graph_writer) 에서 대량 입력.
// 아래는 관계 타입 존재를 보장하기 위한 빈 MERGE 가 아니라,
// writer 가 사용하는 관계 타입을 문서화하는 주석이다.
// (관계 타입은 런타임에 MERGE 로 생성되므로 별도 선언 불필요)

// === 관계 타입 (writer 가 MERGE 로 생성, 멱등) ===
// (Stock)-[:HAS_EVENT]->(Event)
// (Stock)-[:HAS_THEME]->(Theme)
// (Stock)-[:HAS_IMPACT]->(ImpactScore)
// (Event)-[:CO_OCCURS]->(Event)
// (Stock)-[:CO_EVENT]->(Stock)
