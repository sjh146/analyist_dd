#!/bin/sh
# cg62_run.sh — 배포 모델 선택 판정: 현재 배포 챔피언 vs 배포 가능 최선 후보 (10시드 짝)
#
# 질문: 2026-10-01 21:26 게이트가 승격한 챔피언(단일 val split +0.0035)은, 프로토콜 잡음
#       (창 구성 +0.0261 · 유니버스 시드 +0.0211)의 1/7 마진으로 교체된 모델이다. CG61 이
#       10시드 짝으로 그 승격이 '개선 아님'(Δ-0.0097 · t -1.66)임을 확정했다.
#       그렇다면 **지금 실제로 배포된 모델**과 **우리가 가진 배포 가능 최선 후보
#       (cand_cg51 = h5 시장상대 라벨 · 200종목 · 90일 · 추론 계약 무변경)** 중 어느 쪽이
#       생산 프로토콜에서 더 좋은가? — 승격/교체 판단의 유일한 정직한 근거.
#
# 프로토콜(사전 등록 · CG51/CG53 과 동일 계열)
#  - 대조군 arm champ = app/models/champion (10-01 학습분, 단일 val split 으로 게이트 승격)
#  - 챌린저 arm cand  = app/models/cand_cg51 (rel h5 라벨 재학습 · 추론 계약 무변경)
#  - 창 고정 --train-start 2026-06-25 --train-end 2026-10-01 (두 학습구간을 모두 덮어 양쪽 다 OOS)
#  - 라벨 rel h5 (시장상대 중앙값 — 배포/평가 프로토콜의 표준) · 유니버스 시드 0..9
#  - 판정은 집계기 paired 하나뿐. 첫 arm(champ)=대조군 → Δ = cand − champ.
# 사전 등록 성공 기준
#  - 신호: Δ ≥ +0.02 **그리고** 양(+) 시드 10/10 → '배포 모델 교체' 사람 승인 요청 근거
#  - 노이즈: Δ < +0.02 → 배포 가능 축 종결(현 피처풀로는 배포 경로가 문턱을 못 넘는다)
# 한계: 남는 OOS 창은 학습구간 **이전**(2025-12~2026-05)뿐 — 전방 워크포워드가 아니다.
#       champ 는 abs h1 로 학습돼 rel h5 채점에서 off-task 이고, cand 는 rel h5 로 학습돼
#       자기 과제로 채점된다 — 라벨 정합 비교(CG51/CG53 과 같은 설계)이며 보고에 명시한다.
set -e
AGG=/app/reports/cg62_summary.json
while [ $# -gt 0 ]; do
  case "$1" in
    --agg-out) AGG="$2"; shift 2 ;;
    *) shift ;;
  esac
done
cd /app

TSTART=2026-06-25
TEND=2026-10-01
SEEDS="0 1 2 3 4 5 6 7 8 9"

# 같은 **날** 산출만 재사용(창·유니버스가 실행일에 의존 — 다른 날 JSON 재사용은 Δ 오염).
have_json() {
  [ -s "$1" ] || return 1
  python -c 'import json,sys,datetime
try:
    d = json.load(open(sys.argv[1]))
    when = datetime.datetime.fromisoformat(d.get("measured_at") or "").date()
except Exception:
    sys.exit(1)
sys.exit(0 if when == datetime.datetime.utcnow().date() else 1)' "$1"
}

for s in $SEEDS; do
  for spec in "champ:app/models/champion" "cand:app/models/cand_cg51"; do
    tag="${spec%%:*}"; md="${spec##*:}"
    out="app/reports/cg62_${tag}_s$s.json"
    if have_json "$out"; then
      echo "=== eval $tag seed $s — 재사용(오늘 산출) ==="
    else
      echo "=== eval $tag seed $s ==="
      OMP_NUM_THREADS=2 timeout 900 python -u scripts/champion_robust_eval.py \
        --model-dir "$md" \
        --train-start "$TSTART" --train-end "$TEND" \
        --universe training --label-kind rel --horizon 5 \
        --folds 5 --dates-per-fold 10 --stocks 60 --universe-seed "$s" \
        --out "$out"
    fi
  done
done

echo "=== 집계(champ=대조군, cand=챌린저 · 기대 시드 10) ==="
# 셸 변수로 --out 을 만들지 않는다(구동기가 커맨드 문자열에서 첫 --out 만 읽는다 — CG55 함정).
python scripts/champion_seed_family_agg.py --agg-out "$AGG" --expect-seeds 10 \
  --arm champ app/reports/cg62_champ_s0.json app/reports/cg62_champ_s1.json \
              app/reports/cg62_champ_s2.json app/reports/cg62_champ_s3.json \
              app/reports/cg62_champ_s4.json app/reports/cg62_champ_s5.json \
              app/reports/cg62_champ_s6.json app/reports/cg62_champ_s7.json \
              app/reports/cg62_champ_s8.json app/reports/cg62_champ_s9.json \
  --arm cand  app/reports/cg62_cand_s0.json  app/reports/cg62_cand_s1.json \
              app/reports/cg62_cand_s2.json  app/reports/cg62_cand_s3.json \
              app/reports/cg62_cand_s4.json  app/reports/cg62_cand_s5.json \
              app/reports/cg62_cand_s6.json  app/reports/cg62_cand_s7.json \
              app/reports/cg62_cand_s8.json  app/reports/cg62_cand_s9.json
echo "=== 완료: $AGG ==="
