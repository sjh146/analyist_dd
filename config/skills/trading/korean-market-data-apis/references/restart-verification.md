# 재부팅·재가동 직후 점검 명령 묶음

수집 스택을 호스팅하는 머신(WSL 등)을 재시작한 뒤 "확인해줘" 를 받았을 때 한 번에 돌리는 블록.
경로는 스택마다 다르니 `$STACK` 만 바꿔 쓴다. 해석 규칙은 SKILL.md §6 참고.

```bash
STACK=/home/<user>/<repo>; CRON_NAME=<cron.d 파일명>; cd "$STACK"

# 0) 부팅 시각 — 이 시각보다 이른 크론 회차는 실행되지 않았다
uptime; date

# 1) 컴포즈 컨테이너(재시작 정책으로 부팅만으로 뜨는지)
sg docker -c 'docker ps --format "{{.Names}} {{.Status}} {{.RunningFor}}"'
sg docker -c 'docker ps --format "{{.Names}}"' | while read -r c; do
  st=$(sg docker -c "docker inspect --format '{{.RestartCount}} {{.State.Health.Status}}' $c" 2>/dev/null)
  echo "$c $st"
done

# 2) 자동 복귀 보장 (active 뿐 아니라 enabled 여야 함)
for s in cron docker <피드 서비스>.service; do
  printf "%-20s active=%-8s enabled=%s\n" $s "$(systemctl is-active $s)" "$(systemctl is-enabled $s)"
done

# 3) 엔드포인트 헬스(수집/서빙 포트를 모두 나열)
for p in 8000/health 8090/health; do
  printf "%-14s %s\n" "$p" "$(curl -s -m 5 -o /dev/null -w '%{http_code}' 127.0.0.1:$p)"
done

# 4) 크론이 가리키는 스크립트 실존 검사 (없으면 조용히 실패한다)
for f in $(grep -oE '/home/[a-z]+/cron/[a-z_]+\.sh' /etc/cron.d/$CRON_NAME | sort -u); do
  [ -x "$f" ] && echo "OK      $f" || echo "MISSING $f"
done

# 5) 데이터 신선도 — 최신일이 D-1 이면 정상(KRX 는 D+1 공표)
docker exec stock_postgres psql -U stock_user -d stock_trading -tAc \
  "SELECT trade_date, count(*) FROM market_data WHERE trade_date >= current_date - 7 GROUP BY 1 ORDER BY 1"

# 6) 수집 자격증명 살아있나 (1콜 프로브 — 캐시 토큰만 보면 오판한다)
set -a; . .env; set +a
(cd services/kis-collector && /usr/bin/python3 -m kis_app.main --probe-token)

# 7) 크론 로그의 실제 실행 이력
for f in reports/cron_*.log; do echo "--- $f"; ls -l "$f"; tail -5 "$f"; done
```

## 로그를 잘못 읽기 쉬운 지점

- `완료: 0영업일 적재, 0행 upsert, 호출 0회` → 요청 구간이 이미 DB 에 있으므로 아무것도 안 한 **정상 종료**. 실패가 아니다.
- `경로: KRX OpenAPI (KIS 키 없음)` 뒤 `중단: HTTP 401 Unauthorized API Call` → 키는 있으나 서비스 미승인(콘솔 승인 필요). `Unauthorized Key` 면 키 자체가 없거나 잘림. 둘 다 즉시 종료(exit 3)가 정상 동작이다.
- 디스패처 로그에 `경로: KIS ...` 가 없으면 KIS 폴백은 쓰이지 않았다 — "KIS 로 돌았다/안 돌았다"를 로그로만 판단한다.
- 산출물 파일(피드 스냅샷 등)의 mtime 이 오래됐으면 발행 크론이 안 돈 것이다. **크론을 나중에 등록했다면 등록 이전 시각의 회차는 영원히 실행되지 않으므로** 손으로 한 번 돌린다.
- 야간 파이프라인 로그(`full_pipeline_*.log`) 가 없는 것은 "한 번도 안 돌았다"는 뜻이다. 첫 실행은 수집 단계만 30~60분이 걸리므로 로그가 늦게 채워지는 것을 실패로 오판하지 말고, 수집 단계 완료를 알리는 줄이 나타날 때까지 백그라운드로 감시한다.
