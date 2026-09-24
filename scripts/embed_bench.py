#!/usr/bin/env python3
"""CPU 임베딩 처리량 벤치마크 (scripts/embed_bench.py).

`sentence-transformers` 를 CPU 로 돌려 문장/초 처리량을 측정한다. 단순한
"초당 몇 문장" 수치뿐 아니라 **경합(contention) 여부**를 반드시 함께 보고한다.
다른 컨테이너(예: stock_xgboost_ml 학습)가 CPU 를 점유 중이면 측정 수치는
오염되므로, 결과에 ``CONTENDED`` 라벨을 붙여 "유휴 시 재측정 필요"로 표기한다.

실행 방법 (호스트에서):
  # news-analyzer 컨테이너는 compose 가 ./scripts 를 /app/scripts 로 마운트한다.
  docker exec -w /app stock_news_analyzer python scripts/embed_bench.py \
      --threads 1,2,4 --batch 32 --n 256 --json-out /tmp/embed_bench.json
  # 호스트 reports/ 로 결과 JSON 을 꺼낸다.
  docker cp stock_news_analyzer:/tmp/embed_bench.json reports/embed_bench_<ts>.json

의존성: torch + sentence-transformers (news-analyzer 이미지에 포함). psutil 은
사용하지 않으며 CPU 사용률은 /proc/stat 에서 직접 계산한다.
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime

DEFAULT_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"

# 임베딩용 합성 문장(뉴스 유사 텍스트). 벤치마크는 단어/길이 변화에 크게
# 민감하지 않으므로 고정 문장 풀을 순환해 쓴다.
SENTENCE_POOL = [
    "삼성전자가 2분기 실적 발표에서 영업이익이 전년 대비 40% 증가했다고 밝혔다.",
    "미국 연방준비제도가 기준금리를 동결하며 인플레이션 둔화를 언급했다.",
    "국내 증시가 외국인 매수세에 힘입어 코스피 지수가 1% 이상 상승 마감했다.",
    "반도체 수출이 회복세를 보이며 무역수지 흑자 폭이 확대되었다.",
    "정부가 내년도 예산안에서 연구개발 예산을 대폭 확대하기로 결정했다.",
    "The central bank kept interest rates unchanged citing easing inflation.",
    "Tech stocks rallied as chip demand outlook improved sharply.",
    "Oil prices fell after OPEC signaled a potential output increase.",
    "The company reported record quarterly revenue driven by AI demand.",
    "Export growth accelerated as global manufacturing recovered.",
]


def read_cpu_times():
    """/proc/stat 의 'cpu ' 라인에서 (total, idle) 을 읽는다. 실패 시 (None, None)."""
    try:
        with open("/proc/stat", "r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("cpu "):
                    parts = line.split()
                    # user nice system idle iowait irq softirq steal
                    vals = [int(x) for x in parts[1:8]]
                    total = sum(vals)
                    idle = vals[3] + vals[4]  # idle + iowait
                    return total, idle
    except Exception:  # noqa: BLE001 - 벤치마크 도구: 어떤 IO 실패도 삼킨다
        pass
    return None, None


def cpu_busy_pct_between(start, end):
    """(total,idle) 시작/끝 표본으로 해당 구간 CPU 사용률(%)을 계산한다."""
    if None in start or None in end:
        return None
    total_delta = end[0] - start[0]
    idle_delta = end[1] - start[1]
    if total_delta <= 0:
        return None
    return round(100.0 * (1 - idle_delta / total_delta), 1)


def build_sentences(n):
    return [SENTENCE_POOL[i % len(SENTENCE_POOL)] for i in range(n)]


def fmt_seconds(seconds):
    if seconds < 60:
        return f"{seconds:.1f}s"
    if seconds < 3600:
        return f"{seconds / 60:.1f}m"
    return f"{seconds / 3600:.1f}h"


def run_benchmark(model_name, threads_list, batch_size, n_sentences):
    from sentence_transformers import SentenceTransformer

    sentences = build_sentences(n_sentences)
    cores = os.cpu_count() or 1
    load_before = os.getloadavg()

    print("=" * 72)
    print("CPU 임베딩 처리량 벤치마크")
    print("=" * 72)
    print(f"  모델       : {model_name}")
    print(f"  문장 수    : {n_sentences}")
    print(f"  배치 크기  : {batch_size}")
    print(f"  스레드 목록: {threads_list}")
    print(f"  논리 코어  : {cores}")
    print(
        f"  Load avg (직전) : 1m={load_before[0]:.2f} "
        f"5m={load_before[1]:.2f} 15m={load_before[2]:.2f}"
    )
    print("  모델 로드 중...")

    model = SentenceTransformer(model_name, device="cpu")

    results = []
    for threads in threads_list:
        import torch

        torch.set_num_threads(threads)
        cpu_start = read_cpu_times()
        t0 = time.perf_counter()
        model.encode(
            sentences,
            batch_size=batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        elapsed = time.perf_counter() - t0
        cpu_end = read_cpu_times()
        busy_pct = cpu_busy_pct_between(cpu_start, cpu_end)

        sent_per_sec = n_sentences / elapsed if elapsed > 0 else 0.0
        result = {
            "threads": threads,
            "elapsed_sec": round(elapsed, 3),
            "sentences_per_sec": round(sent_per_sec, 2),
            "cpu_busy_pct": busy_pct,
            "seconds_10k": round(10000 / sent_per_sec, 1) if sent_per_sec else None,
            "seconds_100k": round(100000 / sent_per_sec, 1) if sent_per_sec else None,
            "seconds_1m": round(1000000 / sent_per_sec, 1) if sent_per_sec else None,
        }
        results.append(result)

        print("-" * 72)
        print(f"  threads={threads:>2} : {sent_per_sec:6.2f} 문장/초 "
              f"(elapsed={elapsed:.2f}s, CPU사용률={busy_pct}%)")
        print(
            f"             환산: 1만건 {fmt_seconds(result['seconds_10k'])}, "
            f"10만건 {fmt_seconds(result['seconds_100k'])}, "
            f"100만건 {fmt_seconds(result['seconds_1m'])}"
        )

    load_after = os.getloadavg()
    print("-" * 72)
    print(
        f"  Load avg (직후) : 1m={load_after[0]:.2f} "
        f"5m={load_after[1]:.2f} 15m={load_after[2]:.2f}"
    )

    threshold = cores * 0.5
    contended = load_before[0] >= threshold or load_after[0] >= threshold
    print("=" * 72)
    if contended:
        print(
            "  [CONTENDED] 측정 전/후 load average 가 코어 수의 50% "
            f"({threshold:.1f}) 이상 → 이 수치는 경합 상태이며 신뢰 불가."
        )
        print("  유휴 상태에서 재측정할 것 (다른 컨테이너 CPU 점유 종료 후).")
    else:
        print(
            f"  [IDLE] load average 가 코어 수의 50%({threshold:.1f}) 미만 "
            "→ 유휴 상태 측정으로 간주."
        )
    print("=" * 72)

    return {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "model": model_name,
        "batch_size": batch_size,
        "n_sentences": n_sentences,
        "cores": cores,
        "contended": contended,
        "contended_threshold": threshold,
        "load_avg_before": list(load_before),
        "load_avg_after": list(load_after),
        "results": results,
    }


def main():
    parser = argparse.ArgumentParser(description="CPU 임베딩 처리량 벤치마크")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="임베딩 모델명")
    parser.add_argument(
        "--threads",
        default="1,2,4",
        help="측정할 torch 스레드 수 목록 (콤마 구분, 예: 1,2,4)",
    )
    parser.add_argument("--batch", type=int, default=32, help="인코딩 배치 크기")
    parser.add_argument("--n", type=int, default=256, help="인코딩할 문장 수")
    parser.add_argument(
        "--json-out",
        default=None,
        help="결과 JSON 저장 경로 (기본: reports/embed_bench_<ts>.json)",
    )
    args = parser.parse_args()

    try:
        threads_list = [int(x.strip()) for x in args.threads.split(",") if x.strip()]
    except ValueError:
        print(f"오류: --threads 값이 잘못되었습니다: {args.threads}", file=sys.stderr)
        sys.exit(2)
    if not threads_list or any(t <= 0 for t in threads_list):
        print("오류: --threads 는 양의 정수 목록이어야 합니다.", file=sys.stderr)
        sys.exit(2)

    summary = run_benchmark(args.model, threads_list, args.batch, args.n)

    out_path = args.json_out
    if out_path is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = os.path.join("reports", f"embed_bench_{ts}.json")

    out_dir = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(out_dir, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
    print(f"\n결과 JSON 저장: {out_path}")


if __name__ == "__main__":
    main()
