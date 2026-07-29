import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    """Application configuration."""

    # Render 提供 DATABASE_URL (postgresql://user:pass@host:port/db)
    DATABASE_URL = os.getenv("DATABASE_URL", None)

    # 也支持单独配置
    DB_HOST = os.getenv("DB_HOST", "localhost")
    DB_PORT = int(os.getenv("DB_PORT", "5432"))
    DB_NAME = os.getenv("DB_NAME", "mirna_db")
    DB_USER = os.getenv("DB_USER", "postgres")
    DB_PASSWORD = os.getenv("DB_PASSWORD", "123456")

    # Flask
    SECRET_KEY = os.getenv("SECRET_KEY", os.urandom(24).hex())

    @staticmethod
    def get_dsn():
        # Render 环境优先使用 DATABASE_URL
        database_url = Config.DATABASE_URL
        if database_url:
            # Render 的 DATABASE_URL 格式是 postgresql://... 需要转成 psycopg2 兼容格式
            if database_url.startswith("postgresql://"):
                return database_url.replace("postgresql://", "postgres://", 1)
            return database_url
        return (
            f"host={Config.DB_HOST} port={Config.DB_PORT} "
            f"dbname={Config.DB_NAME} user={Config.DB_USER} "
            f"password={Config.DB_PASSWORD}"
        )
