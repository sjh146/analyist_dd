# GPU 준비 가이드 (GPU Readiness)

현재는 **CPU 경로**로 동작한다. GPU 가 아직 없기 때문에 아무것도 바꾸지 않고,
나중에 GPU 가 생겼을 때 **코드 수정 없이** 켤 수 있는 스위치·오버레이·진단만
준비해 둔다.

## 현재 상태 (2026-09 기준)

| 항목 | 상태 |
| --- | --- |
| 호스트 (WSL) | 4코어, RAM 7.9GB |
| `/dev/dxg` | 존재 (WSL GPU 게이트웨이) |
| `nvidia-smi` / `nvcc` | **없음** → CUDA 사용 불가 |
| 컨테이너 torch | `2.5.1+cpu`, `torch.cuda.is_available() = False` |
| docker-compose | GPU 설정 없음 (본체) |
| 임베딩 | `paraphrase-multilingual-MiniLM-L12-v2` (384차원), CPU 로드 |
| LLM | news-analyzer 가 DeepSeek API 사용 (`LLM_BASE_URL` 로 로컬 전환 가능) |

**즉 지금은 GPU 가 없으므로 기존 임베딩/분석 경로(CPU)가 그대로 동작한다.**

## 준비해 둔 것 (이번 작업)

| 항목 | 위치 |
| --- | --- |
| 임베딩 디바이스 자동 선택 | `services/news-analyzer/app/embedding/device.py`, `services/stock-vectorizer/models/device.py` |
| env 스위치 | `EMBEDDING_DEVICE`(auto/cuda/cpu/mps), `EMBEDDING_BATCH_SIZE`(기본 32), `EMBEDDING_TORCH_THREADS`(기본 미설정), `LLM_MODEL` |
| GPU 오버레이 | `docker-compose.gpu.yml` (기본 비활성) |
| 진단 도구 | `scripts/gpu_doctor.py` |
| env 예시 | `.env.example` |

### `EMBEDDING_DEVICE` 동작

- `auto` (기본): `torch.cuda.is_available()` 이면 `cuda`, 아니면 `cpu`. (mps 지원 시 mps)
- `cuda` / `cpu` / `mps`: 명시 지정.
- `cuda` 를 명시했는데 사용 불가하면 **조용히 CPU 로 떨어지지 않고 경고 로그를 남긴 뒤 CPU 폴백** (운영은 멈추지 않음).
- 알 수 없는 값이면 `cpu` + 경고.

## GPU 가 생겼을 때 순서

### ① 드라이버 / 툴킷 설치 (호스트)

WSL 에서 CUDA 를 쓰려면:

1. **Windows 호스트**: NVIDIA 드라이버 + (Docker Desktop 사용 시) WSL2 백엔드 활성화
2. **Docker Engine 직접 설치** 시 WSL 배포판 안에 `nvidia-container-toolkit` 설치
3. 검증:

```bash
docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi
```

### ② GPU 오버레이로 기동

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d
```

`docker-compose.yml` 본체는 수정하지 않는다. 오버레이는 `news-analyzer` 와
`stock-vectorizer` 에만 `deploy.resources.reservations.devices` 로 NVIDIA GPU 를
연결한다.

> 참고: `xgboost-ml`(GBDT)은 GPU 이득이 크지 않아 기본 제외. 필요 시
> `docker-compose.gpu.yml` 내 주석을 풀어 연결할 수 있다.

### ③ 진단 통과 확인

```bash
python3 scripts/gpu_doctor.py
# GPU 세팅 검증(실패 시 비영 종료)용:
python3 scripts/gpu_doctor.py --require-gpu
```

`--require-gpu` 가 exit 0 을 반환해야 GPU 세팅이 정상이라는 뜻.

### ④ `EMBEDDING_DEVICE=auto` 로 충분한지 확인

GPU 오버레이를 켜고 컨테이너가 `torch.cuda.is_available() = True` 를 보고하면,
`EMBEDDING_DEVICE=auto` (기본값) 만으로 자동으로 `cuda` 를 사용한다. `.env` 를
수정할 필요가 없다.

원하면 명시적으로 강제할 수도 있다:

```env
EMBEDDING_DEVICE=cuda
EMBEDDING_BATCH_SIZE=64
```

이때 cuda 가 없으면 경고 로그 + CPU 폴백이므로, 오동작 없이 안전하다.

### ⑤ (선택) 로컬 LLM 추론 전환

GPU 를 LLM 추론에도 쓰려면 llama.cpp CUDA 빌드로 로컬 서버를 띄우고,
news-analyzer 를 DeepSeek API 대신 로컬로 전환한다.

```env
LLM_BASE_URL=http://host.docker.internal:8080/v1
LLM_MODEL=<로컬 서버 모델명>   # 비워두면 DEEPSEEK_MODEL 사용
```

`LLM_MODEL` 을 비워두면 기존 `DEEPSEEK_MODEL` 을 그대로 쓰므로 **기본 동작은
DeepSeek API 유지**.

## 되돌리기 (현행 CPU 복귀)

GPU 오버레이 없이 기동하면 그대로 현행 CPU 경로로 돌아온다:

```bash
docker compose -f docker-compose.yml up -d
```

`EMBEDDING_DEVICE`/`EMBEDDING_BATCH_SIZE`/`EMBEDDING_TORCH_THREADS`/`LLM_MODEL` 을
`.env` 에 넣지 않으면 기본값(auto/32/미설정/빈값)이 적용되어 현재와 동일하게
동작한다.

## CPU 스레드 튜닝

### `EMBEDDING_TORCH_THREADS`

CPU 임베딩의 torch 스레드 수를 제어한다. **기본은 미설정**이며, 이 경우
`torch.set_num_threads` 를 호출하지 않으므로 torch 기본 동작(현행) 그대로다.

```env
# 양의 정수만 유효. 미설정 시 torch 기본 동작 유지.
EMBEDDING_TORCH_THREADS=1
```

- 설정하면 모델 로드 직전 `torch.set_num_threads(n)` 이 적용되고, 선택된
  스레드 수가 1회 INFO 로그로 남는다.
- `0` / 음수 / 비정수는 무시하고 `None`(torch 기본)으로 처리하되 `UserWarning`
  경고를 남긴다.
- 해석 로직은 `services/news-analyzer/app/embedding/device.py`,
  `services/stock-vectorizer/models/device.py` 의 `resolve_torch_threads` 에 있다.

### 경합 시 역효과 주의

**다른 컨테이너가 CPU 를 점유 중일 때 스레드를 늘리면 오히려 느려진다.**
실측 사례(경합 상태, 신뢰 불가 — 아래 라벨 참고):

| threads | 문장/초 | 비고 |
| --- | --- | --- |
| 1 | 19.7 | **CONTENDED** — `stock_xgboost_ml` 이 CPU 약 54% 점유 중 |
| 2 | 15.9 | CONTENDED |
| 4 | 6.1 | CONTENDED |

스레드 증가 구간 수치는 경합 오염으로 신뢰 불가하며, 단일 스레드 약 20문장/초
만 유효한 보수값이다. **유휴 상태에서 재측정해야 실제 최적 스레드 수를 알 수
있다.**

### 유휴 상태 재측정 (`embed_bench.py`)

`scripts/embed_bench.py` 는 처리량과 함께 측정 직전/직후 load average 와 실행 중
CPU 사용률을 출력하고, load 가 코어 수의 50% 이상이면 결과에 `CONTENDED`
라벨을 붙인다. 경합 상태 숫자를 조용히 보고하지 않는다.

```bash
# news-analyzer 컨테이너는 compose 가 ./scripts 를 /app/scripts 로 마운트한다.
docker exec -w /app stock_news_analyzer python scripts/embed_bench.py \
    --threads 1,2,4 --batch 32 --n 256 --json-out /tmp/embed_bench.json

# 호스트 reports/ 로 결과 JSON 을 꺼낸다.
docker cp stock_news_analyzer:/tmp/embed_bench.json reports/embed_bench_<ts>.json
```

`CONTENDED` 라벨이 붙으면 "유휴 시 재측정 필요"로 판단하고, `stock_xgboost_ml`
등 학습 컨테이너가 끝난 뒤 다시 돌린다.

## 무료 오픈소스 임베딩으로 교체하려면

현재 임베딩은 `paraphrase-multilingual-MiniLM-L12-v2`(384차원)이다. 더 나은
품질/다국어 지원을 위해 `BAAI/bge-m3`(1024차원) 같은 무료 오픈소스 모델로
교체하려면 아래 선행 조건을 먼저 충족해야 한다.

### ① torch ≥ 2.6 필요 (CVE-2025-32434)

현재 컨테이너 torch 는 `2.5.1+cpu` 이다. `bge-m3` 를 로드하면 다음과 같이
실패한다:

```
ValueError: ... we now require users to upgrade torch to at least v2.6
```

이는 `torch.load` 의 `weights_only` 기본값 변경(CVE-2025-32434)과 관련된 버전
요구다. `model_kwargs={"use_safetensors": True}` 로도 해결되지 않는다
(`bge-m3` 레포에 `model.safetensors` 가 없고 `pytorch_model.bin` 만 존재):

```
OSError: BAAI/bge-m3 does not appear to have a file named model.safetensors
```

→ `services/news-analyzer/requirements.txt`(및 stock-vectorizer)의
`torch==2.5.1+cpu` 를 `torch>=2.6.0+cpu` 로 올려야 한다.

### ② 차원 변경 시 스키마 동반 변경 필수

**현재 불일치**: `.env` 의 `VECTOR_DIMENSION=1024` vs 실제 사용 모델
`paraphrase-multilingual-MiniLM-L12-v2` 는 **384차원**. `bge-m3`(1024차원)로
바꾸면 차원이 384 → 1024 로 바뀐다.

- `services/stock-vectorizer/app/config.py:18` 의
  `VECTOR_DIMENSION = int(os.getenv("VECTOR_DIMENSION", "1024"))` 기본값이
  1024 인 반면, 실제 인코딩은 384차원이라 **이미 불일치 상태**다.
- DB 스키마도 고정 차원으로 잡혀 있다:
  - `init-scripts/postgres/01_schema.sql:40` → `stock_vectors.embedding vector(1024)`
  - `init-scripts/postgres/01_schema.sql:47` → `idx_stock_vectors_hnsw` HNSW 인덱스
  - `init-scripts/postgres/01_schema.sql:221` → `news_analysis.embedding vector(1024)`
  - `init-scripts/postgres/03_news_intelligence.sql:72` → 뉴스 임베딩 `vector(384)`

pgvector 는 차원이 다른 벡터를 같은 컬럼에 넣을 수 없으므로, 모델 차원 변경
시 테이블·인덱스를 새 차원에 맞춰 마이그레이션해야 한다. 뉴스(384)와 종목
벡터(1024)는 애초에 다른 차원이므로, 어떤 모델을 어디에 쓸지 정리하는 것부터
시작한다.

### ③ 교체 절차 순서

1. **차원 결정**: 교체 모델의 출력 차원을 확인하고, 종목 벡터/뉴스 임베딩 각
   컬럼의 목표 차원을 정한다.
2. **torch 업그레이드**: 두 서비스 `requirements.txt` 의 torch 를 `>=2.6.0+cpu`
   로 올리고 이미지 재빌드.
3. **DB 마이그레이션**: 기존 벡터 데이터를 보존하려면 새 차원 컬럼 추가 →
   재인코딩 적재 → 기존 컬럼/인덱스 교체 순서로 진행(하나의 ALTER 로 바꾸면
   기존 벡터가 유실될 수 있다).
4. **`.env` 차원 정합**: `VECTOR_DIMENSION` 과 `news_embedder.EMBEDDING_DIM`
   등 실제 모델 차원을 일치시킨다.
5. **검증**:
   ```bash
   docker exec stock_news_analyzer python -c \
     "from sentence_transformers import SentenceTransformer; m=SentenceTransformer('<새 모델>'); print(m.get_sentence_embedding_dimension())"
   docker exec stock_vectorizer python -c "from app.config import Config; print(Config.VECTOR_DIMENSION)"
   ```
   차원 값이 일치하는지, 그리고 벡터 적재 후 유사도 검색(`find_similar_stocks`)
   이 정상 동작하는지 확인한다.

## 한계

- **4코어 CPU 기준 임베딩 처리량**: `paraphrase-multilingual-MiniLM-L12-v2` 는
  CPU 에서도 충분히 돌지만, GPU 대비 배치 처리량이 낮다. 뉴스 이벤트 클러스터링
  임베딩(소량)은 CPU 로도 문제없다.
- **GBDT 학습(`xgboost-ml`)은 GPU 이득이 크지 않다**: lightgbm/xgboost/catboost 의
  GPU 히스토그램 빌더는 데이터셋이 크지 않으면 CPU 대비 이득이 미미하다.
  GPU 오버레이는 임베딩·딥러닝 경로에 먼저 써라.
