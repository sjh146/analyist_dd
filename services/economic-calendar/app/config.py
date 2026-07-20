import os


class Config:
    DB_HOST: str = os.getenv("POSTGRES_HOST", "postgres")
    DB_PORT: int = int(os.getenv("POSTGRES_PORT", "5432"))
    DB_NAME: str = os.getenv("POSTGRES_DB", "stock_trading")
    DB_USER: str = os.getenv("POSTGRES_USER", "stock_user")
    DB_PASSWORD: str = os.getenv("POSTGRES_PASSWORD", "stock_password")

    @property
    def db_url(self) -> str:
        return (
            f"host={self.DB_HOST} port={self.DB_PORT} "
            f"dbname={self.DB_NAME} user={self.DB_USER} password={self.DB_PASSWORD}"
        )
