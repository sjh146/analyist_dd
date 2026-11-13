# 장중(월요일) 실검증 체크리스트 — Creon 모의 매매

> 대상: Windows 노트북(jhshi) + BC-250. 목적: Phase 2b 잔여(실시간 틱 + 모의 주문
> 라운드트립) + Phase 2c(/balance 현금 필드, 주문 요청제한 게이트) + trader-core 실연결 검증.
> 추정 소요: 장중 20~40분. 진행자는 bc-250 에이전트(원격) + 사용자(로컬 UAC/화면 확인).

## 사전 조건 (장 개시 전)

1. **브리지 재시작 (필수)** — 노트북에 떠 있는 브리지는 Phase 2b 시절 코드(현금 필드 없음).
   - 사용자: `C:\Users\jhshi\analyist_dd\trader-agent\bridge_run_admin.bat` 우클릭 → 관리자 권한으로 실행(UAC)
   - 기존 인스턴스가 8100을 점유 중이면 새로 안 뜸 → 먼저 종료:
     ```powershell
     Get-NetTCPConnection -LocalPort 8100 -State Listen | % { Stop-Process -Id $_.OwningProcess -Force }
     ```
2. **HTS 모의 로그인 확인**: CBP.exe 실행 → CYBOSPLUS 선택 + 모의투자 토글(초록) + HTS 창 유지.
3. **BC-250 피드**: `http://100.127.186.48:8080/screener_latest.json` 응답 확인 (전일 19:30 export).
   trader-core `feed_max_age` 기본 24h → 장중에 전일 export fresh.
4. tailscale 양쪽 up.

## 검증 순서 (09:05~15:00)

### A. 브리지 상태 + 현금 필드 (신규: CpTdNew5331A)
```powershell
# 노트북에서 (관리자 PowerShell 권장)
Invoke-RestMethod http://127.0.0.1:8100/health | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:8100/balance  | ConvertTo-Json -Depth 4
```
기대:
- `connected: true`
- `equity` > 0 (CpTd6033 header3 총평가금액)
- `cash` = 모의 현금주문가능금액(5331A header10). **모의서버가 5331A 미지원이면**
  `buyable_error` 포함 + `cash`가 `d2_deposit` 폴백 — 기록 후 계속(폴백 정상 동작이므로).

### B. 실시간 틱 수신 (Phase 2b 잔여 1)
```powershell
$b = @{codes=@('005930','000660')} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8100/subscribe -Body $b -ContentType 'application/json'
Start-Sleep -Seconds 10
Invoke-RestMethod http://127.0.0.1:8100/quotes | ConvertTo-Json
```
기대: `quotes`에 두 종목 현재가/체결시간(장중 tick) — 비어 있으면 30초 후 재시도, 그래도 없으면
브리지 로그(`bridge.err.log`) 확인.

### C. trader-core 실연결 (64bit)
```powershell
# 노트북, 64bit Python
C:\Users\jhshi\Python312-64\python.exe C:\Users\jhshi\analyist_dd\trader-agent\run_trader_core.py status
C:\Users\jhshi\Python312-64\python.exe C:\Users\jhshi\analyist_dd\trader-agent\run_trader_core.py scan --dry-run
```
기대: status에 connected:true + cash(equity) 표시; scan은 전일 시그널(close 등)을 게이트 평가 —
장중이면 market_hours 통과 → 일부 후보에 실제 주문 계획(limit, 캘리 사이즈)이 떠야 함.
(트레이더-core 코드는 repo에서 배포 필요: `bash ~/AUTONOMY/tools/deploy_trader_core.sh` 예정 —
없으면 bridge와 동일하게 scp로 trader_core/ + run_trader_core.py 복사)

### D. 모의 주문 라운드트립 (Phase 2b 잔여 2) — 1주만
장중 현재가 확인 후 지정가 1주 매수 (모의 계좌 7770******):
```powershell
# 가격은 장중 현재가 기준 1틱 위(체결 확률↑) — 예: 현재가 60,000이면 60,050
$o = @{side='buy'; code='005930'; qty=1; price=60050; order_type='limit'} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8100/order -Body $o -ContentType 'application/json' | ConvertTo-Json
Start-Sleep -Seconds 3
Invoke-RestMethod http://127.0.0.1:8100/positions | ConvertTo-Json   # 1주 보유 확인
# 청산 (시장가)
$s = @{side='sell'; code='005930'; qty=1; order_type='market'} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8100/order -Body $s -ContentType 'application/json' | ConvertTo-Json
Start-Sleep -Seconds 3
Invoke-RestMethod http://127.0.0.1:8100/positions | ConvertTo-Json   # 비어 있어야
```
기대: 두 주문 모두 `ok:true` + 체결 반영. **주문 요청제한 게이트**도 이때 함께 검증 —
연속 주문(위 2건) 사이 지연 없이 통과하면 정상(1초 예산 내). 거부 시 `rate_limited`/ret=4 로그 기록.

### E. 킬스위치 + stale 피드 (fail-closed 확인)
```powershell
# 노트북 trader-agent 폴더에 파일 생성
New-Item kill_switch.txt -ItemType File
# scan 시 전 후보 "kill_switch" 게이트로 차단 확인 → 제거
Remove-Item kill_switch.txt
```

## 완료 기준 / 결과 기록
- [ ] A: cash/equity 정상 (또는 buyable_error 폴백 기록)
- [ ] B: 실시간 틱 수신
- [ ] C: trader-core status/scan 게이트 평가 동작
- [ ] D: 매수 1주 체결 → 보유 반영 → 시장가 청산 → 잔고 0
- [ ] E: 킬스위치/차단 동작
- 결과는 ~/AUTONOMY/CREON_TRADER_PLAN.md 진행 기록에 반영 (bc-250 에이전트 담당)

## 장애 시
- 주문 이상 → 즉시 취소: `/orders`로 미체결 조회 → `/cancel` {order_no, code}
- 브리지 응답 없음 → `bridge.err.log` 확인, 관리자 재시작
- 5331A 미지원(모의) → B~D는 계속 진행, A의 폴백 동작만 기록
