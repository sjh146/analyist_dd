# Proxmox 2-VM 아키텍처 로드맵 — 리서처(Ubuntu) + 트레이더(Windows/Creon)

> 작성: 2026-08-25 (사용자 구상 확정 — "두 에이전트가 브릿지통신으로 소통하며
> 트레이더가 리서처 정보를 받아 매매")
> 상태: **계획** — 실행 전 데이터 백업 필수 (Proxmox = 호스트 OS 교체)

## 1. 목표 아키텍처

```
┌─ Proxmox VE (호스트 — i7-6500U 4스레드 / RAM 15GB / SSD 234G) ────────────┐
│                                                                           │
│  [Ubuntu VM] 리서처 에이전트             [Windows VM] 트레이더 에이전트     │
│  ─────────────────────────────          ─────────────────────────────      │
│  · analyist_dd 전체 스택                · Creon API (키움 OpenAPI —        │
│    (postgres/neo4j/jenkins/              Windows 전용 확정)                │
│     grafana/prometheus/redis)           · 알파 알고리즘 실행부             │
│  · Hermes (리서처)                       · RealCreonExecutor (Mock와       │
│  · 시그널 생성 (칼만/챔피언/SNS)          인터페이스 동일 — 스왑만)         │
│  · 크론 파이프라인 (18:55/20:00)         · Hermes (트레이더)               │
│          │                                 ▲                              │
│          │ ① Redis 브릿지 (trading:signals │  HMAC 서명 인증               │
│          └──────────── ② MCP 브릿지 ──────┘                               │
│                    ③ 게이트웨이 메시징 (Discord 승인 채널)                  │
└───────────────────────────────────────────────────────────────────────────┘
```

**핵심 원칙**: 리서처는 정보 생산(시그널·분석), 트레이더는 실행(주문·리스크) 전담.
사람은 Discord 승인 채널로 감독만.

## 2. 현재 코드와의 정합 (이미 설계돼 있음)

- `scripts/run_mock_executor.sh` 주석: "config.py는 REDIS_HOST가 아니라
  **BRIDGE_HOST/BRIDGE_PORT를 읽음 (Windows VM 브리지 설계)**" — 브릿지 아키텍처 선반영
- `USE_MOCK_CREON=true → MockCreonExecutor` — **RealCreonExecutor로 교체 지점 명시**
  (Creon API는 Windows 전용이므로 Windows VM에서만 존재)
- 시그널 흐름: strategy-agents(30분) → Redis `trading:signals`/`strategy:signals`
  (consumer group) → HMAC(`TRADE_SIGNAL_SECRET`, 미설정=fail-closed) → 주문 실행부
- 시그널 테스트: `scripts/test_mock_signal.py buy|sell` (HMAC 서명 자동)

## 3. 하드웨어 예산 (15GB RAM — 빡빡함)

| 구성 | RAM | 비고 |
|---|---|---|
| Proxmox 호스트 | 2GB | KVM/관리 오버헤드 |
| Ubuntu VM (리서처) | 6GB | neo4j 917MB + jenkins 400MB + postgres + xgboost-ml |
| Windows VM (트레이더) | 4~6GB | **LTSC/최소 구성 권장** (10 LTSC ~4GB) |
| **합계** | **12~14GB** | 동시 풀가동 시 swap 위험 — 초기엔 번갈아 부팅 |

- 디스크: 여유 144GB — Windows 40G + Ubuntu 50G + Proxmox 10G ≈ 100GB (thin provisioning).
- CPU: 4스레드 — 리서처 배치(야간)와 트레이더 장중을 **시간대별로 분리**하면 경합 최소화.
- 장기: RAM 32GB 업그레이드 시 두 VM 동시 풀가동 가능.

## 4. 브릿지 통신 3종 (조합 가능)

| 방식 | 프로토콜 | 용도 | 인증 |
|---|---|---|---|
| ① Redis 시그널 | Redis Streams (consumer group) | 자동 매매 파이프라인 | HMAC (`TRADE_SIGNAL_SECRET`) |
| ② Hermes MCP | MCP stdio/HTTP 서버 | 에이전트 간 협업 (리서처 툴 호출) | Hermes mcp 등록 |
| ③ 게이트웨이 메시징 | Discord 채널 | 사람 승인/감독 + 에이전트 대화 | Discord allowlist |

## 5. 마이그레이션 절차 (Proxmox = 호스트 OS 교체 ⚠️)

**순서 (리스크 최소화)**:
1. **백업 (필수 — 이 단계가 전부)**:
   - Docker 볼륨 14개: postgres `pg_dump` + neo4j dump + jenkins home tar
   - `~/analyist_dd`·`~/cmall_dd` 등 repos: origin 푸시 확인 (이미 푸시됨)
   - `~/.hermes/` (secrets 포함) + `~/.git-credentials` tar
   - DuckDNS 스크립트·크론 목록(`crontab -l`)·`systemctl --user` 유닛
2. **P2V 이전**: Clonezilla로 현재 Ubuntu 디스크 이미지 → Proxmox Ubuntu VM 복원
   (또는 신규 Ubuntu VM + 백업에서 재구축)
3. **Proxmox 설치** (USB 부팅 → ZFS/EXT4 선택, VM 브리지 네트워크)
4. **Ubuntu VM 기동**: docker compose up -d → Tailscale Funnel 복원
   (고정주소 https://hangot.tail7dae99.ts.net — VM 내부에서 재구성)
5. **Windows VM**: Creon 설치 (키움증권 OpenAPI, 계좌 인증) → RealCreonExecutor 구현
6. **브릿지 검증**: mock 시그널 → Windows VM 수신 확인 → 실주문 전 paper 단계 유지

## 6. 단계별 로드맵

| Phase | 내용 | 완료 기준 |
|---|---|---|
| **0 (지금)** | mock 브릿지로 시그널 흐름 검증 | 이미 동작 중 (실행기, HMAC, Redis) |
| **1** | 데이터 백업 + Proxmox 설치 + P2V 이전 | Ubuntu VM에서 전체 스택 동작 |
| **2** | Windows VM 구축 (LTSC + Creon) | Creon 로그인/조회 API 동작 |
| **3** | RealCreonExecutor 구현 (Mock과 동일 인터페이스) | 시그널 → 실제 주문 (paper 먼저) |
| **4** | Redis 브릿지 확정 → MCP 브릿지 추가 | 에이전트 간 협업 + 자동 매매 |
| **5** | (선택) RAM 32GB + 리서처/트레이더 동시 풀가동 | — |

## 7. 리스크

- **최대 리스크: 데이터 유실** — Phase 1 백업 누락 시 볼륨 14개 전부 소실 (pg_dump 검증 필수)
- 하드웨어: RAM/CPU 한계로 동시 부팅 제약 → 시간대 분리 운영
- Creon: 계좌 인증/OTP/모의투자 계좌 필요 — 실계좌 전환 전 모의투자로 충분히 검증
- Windows VM 라이선스: Windows 10 LTSC (볼륨 라이선스) 또는 Pro
- Tailscale/터널: VM 이동 후 funnel 재구성 필요 (cmall 8082/shop 8081 포트 유지)

## 8. 참조

- 현재 mock 실행기: `scripts/run_mock_executor.sh` (BRIDGE_HOST/BRIDGE_PORT)
- 브릿지 설계: `services/strategy-agents/app/config.py` (BRIDGE_*)
- 크레온 추상화: `USE_MOCK_CREON` env → MockCreonExecutor/RealCreonExecutor
- 관련 스킬: analyist-dd-pipeline-ops (mock 섹션), hangot-robot (승인 채널 패턴)
