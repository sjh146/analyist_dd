import os


class Config:
    DB_HOST: str = os.getenv("POSTGRES_HOST", "")
    DB_PORT: int = int(os.getenv("POSTGRES_PORT", ""))
    DB_NAME: str = os.getenv("POSTGRES_DB", "")
    DB_USER: str = os.getenv("POSTGRES_USER", "")
    DB_PASSWORD: str = os.getenv("POSTGRES_PASSWORD", "")

    @property
    def db_url(self) -> str:
        return (
            f"host={self.DB_HOST} port={self.DB_PORT} "
            f"dbname={self.DB_NAME} user={self.DB_USER} password={self.DB_PASSWORD}"
        )
