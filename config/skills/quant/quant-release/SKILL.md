---
name: quant-release
description: "Use when shipping a champion, cron, feed, or image."
---

# quant-release — 릴리스 매니저

역할계획 v2 ⑤. 근거: `docs/QUANT_ROLE_PLAN_V2.md` §3·§5.5. 우리 릴리스는 PR 이 아니라 **산출물 교체**다.

## 대상(하나면 이 절차를 탄다)
`app/models/champion` 교체 · `data/feed/**` 계약 변경 · 크론(`/etc/cron.d`·crontab·Hermes) ·
이미지/`docker-compose` · 실주문 경로 변경.

## 실행 도구 (2026-10-02 구현 — 이걸 쓴다)
- `tools/release_champion.sh --candidate <dir> [--with-probe] [--apply]`
  동결→백업→dry-run→사전검사→(승인 시)적용→즉시/15분 검증→롤백 명령. **리허설이 기본이고 `--apply` 없이는 아무것도 바꾸지 않는다.**
- `tools/release_precheck.py --target champion|feed|cron|image [--candidate <dir>] [--with-probe]`
  감사 4종 + 피드 계약 + 승격 dry-run + (선택)라이브 스코어 프로브 → PASS/BLOCK(rc 0/2).
  실측 예(2026-10-02, 후보 champion_cand): **BLOCK** — 사유 4건 중 "후보의 0.55 초과 신호 0건(max=0.4733)"
  가 MT116 유형을 릴리스 전에 직접 지목한다.

## 절차 (순서 고정 — 건너뛰면 릴리스가 아니다)
1. **동결**: 이 산출물을 읽는 소비자 목록을 `grep` 으로 실측(추정 금지)
2. **백업**: 되돌릴 경로를 정확히 기록(`champion_promote` 는 `champion_prev_<ts>` 자동 생성)
3. **dry-run**: 게이트 CLI 의 `would_*`/`kept_incumbent` 상태를 **문자열 그대로 인용**
4. **적용** — 승인 항목(챔피언 최종 승격·크론·계약)은 대표/리뷰보드 승인 후에만
5. **릴리스 후 15분 검증**: auditor 검사 재실행 → ① `batch_type=signal` ② 피드 전략별 건수>0
   ③ 루프 `last_cycle` 갱신 + 브리지 connected. 하나라도 실패면 6번.
6. **롤백**: 복원 명령 1줄을 남긴다
   (예: `docker exec -w /app stock_xgboost_ml python -m app.training.champion_promote --candidate app/models/champion_prev_<ts> --champion app/models/champion --min-improvement -0.01`)

## 실측 교훈(재사용)
- 챔피언 교체는 val AUC 단독 판정 금지 — 같은 유니버스의 **라이브 스코어 분포**를 함께 본다(MT116)
- 롤백은 게이트가 AUC 단조 개선을 요구하므로 `--min-improvement` 를 음수로 열어야 통과한다(dry-run 확인)
- 승인 경계: 릴리스는 준비·검증·롤백을 소유하고 **승인은 소유하지 않는다**

## 산출물
`docs/releases/<YYYY-MM-DD>-<slug>.md` — 동결 목록 · 백업 경로 · dry-run 인용 · 적용 내용 ·
15분 검증 결과 · 롤백 명령 · 감사 리포트 링크.
