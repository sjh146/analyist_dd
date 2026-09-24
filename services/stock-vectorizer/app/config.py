import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
    POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
    POSTGRES_DB = os.getenv("POSTGRES_DB", "stock_trading")
    POSTGRES_USER = os.getenv("POSTGRES_USER", "stock_user")
    POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "")

    NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
    NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")

    VECTOR_DIMENSION = int(os.getenv("VECTOR_DIMENSION", "1024"))
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

    # 임베딩 실행 디바이스 (GPU 준비용, 기본값 auto = 현행 CPU 동작)
    # - auto: torch.cuda.is_available() 이면 cuda, 아니면 cpu (mps 지원 시 mps)
    # - cuda/cpu/mps: 명시 지정. cuda 를 명시했는데 사용 불가면 경고 후 cpu 폴백.
    EMBEDDING_DEVICE = os.getenv("EMBEDDING_DEVICE", "auto")
    # 임베딩 인코딩 배치 크기
    EMBEDDING_BATCH_SIZE = os.getenv("EMBEDDING_BATCH_SIZE", "32")
    # CPU 임베딩 스레드 수 튜닝용 (기본 미설정 = torch 기본 동작 유지)
    # 양의 정수만 유효하며, 모델 로드 직전 torch.set_num_threads(n) 로 적용된다.
    # 주의: 다른 컨테이너가 CPU 를 점유(경합) 중이면 스레드를 늘려도 오히려 느려질
    # 수 있다. 유휴 상태에서 scripts/embed_bench.py 로 재측정 후 결정할 것.
    EMBEDDING_TORCH_THREADS = os.getenv("EMBEDDING_TORCH_THREADS", "")

    @classmethod
    def get_pg_dsn(cls) -> str:
        return (
            f"host={cls.POSTGRES_HOST} port={cls.POSTGRES_PORT} "
            f"dbname={cls.POSTGRES_DB} user={cls.POSTGRES_USER} "
            f"password={cls.POSTGRES_PASSWORD}"
        )
