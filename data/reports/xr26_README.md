# XR26 분봉 페이지네이션 — 적용 지침 (단일 권위 패치)

**적용할 패치 (하나뿐이다):** `data/reports/xr26_minute_pagination.patch`
대상: `services/kis-collector/kis_app/collectors/minute_collector.py` (수집기 소유 → 이 역할은 산출물만 만든다)

```bash
cd /home/jhshi/analyist_dd
git apply --check -p1 data/reports/xr26_minute_pagination.patch   # rc=0 (대상 파일 무변경)
git apply -p1        data/reports/xr26_minute_pagination.patch   # 승인 후 실제 적용
bash scripts/_xr26_verify.sh                                      # 적용 사본으로 12/12 PASS + 자동 원복
python3 scripts/_xr26_fix_verify.py                               # 7/7 PASS (사본 적용·무해)
```

## 이력 (중요)
- 2026-10-08: `xr26_minute_pagination_fix.patch` 를 만들었다(7/7 PASS).
- 2026-10-09 02:0x: 종료 로직을 다시 손본 `xr26_minute_pagination.patch` 를 추가 생성(12/12 PASS).
- → **같은 파일을 고치는 diff 두 개가 동시에 존재**해 승인자가 어느 것을 적용해야 하는지 알 수 없었다
  (적용 실수 시: 30봉 조기종료가 남거나, 반대로 전 구간을 못 받는다).
- 2026-10-09 04:0x: 최신 종료 로직(개장 도달 = 종료 · 진행 없음 = 무한루프 방어)을 기준으로
  `MAX_PAGES_DEFAULT=20` · `FID_CNT_DEFAULT=30`(실측 상한 문서화) · docstring 설명을 합쳐 **하나로 단일화**했고,
  두 검증기(`_xr26_verify.sh` 12/12 · `_xr26_fix_verify.py` 7/7)가 **같은 패치**를 통과한다.
  구 패치는 오적용 방지를 위해 `xr26_minute_pagination_fix.patch.superseded` 로 이름을 바꿨다(내용은 보존).

## 실측 근거 (2026-10-09 04:0x 재검증)
| 항목 | 현행(미적용) | 패치 후 |
|---|---|---|
| 기본 경로 수집 봉수 | **30봉** (15:01~15:30) | **391봉** (09:00~15:30) |
| KIS 호출 수 | 1회 | 14회 (= ceil(391/30)) |
| 비정형 페이지(57행) | 30봉 | 391봉 |
| `minute_bars` 실측 | 72,120행 · 일 300종목×30봉 | (수리 후 백필 필요) |

⚠ 수리 후에도 이력은 **전방 누적만** 가능(수집기 설계 = 당일 1회) → 인트라데이 축(CG101·CG129) 판정은
수리 시점 +약 20거래일. 호출량 = 종목당 1 → 14콜(300종목 ≈ 4,200콜/일) → net_guard 간격 정책 안에서
마감 후 분할 스케줄은 **수집기 소유자**가 정한다.
