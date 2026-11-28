from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List, Optional


class Settings(BaseSettings):
    """Cấu hình biến môi trường toàn hệ thống."""
    model_config = SettingsConfigDict(
        env_file=(".env", "backend/.env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # General
    PROJECT_NAME: str = "Enterprise AI Knowledge Platform"
    ENVIRONMENT: str = "development"
    DEBUG: bool = False
    API_V1_PREFIX: str = "/api/v1"

    # Security
    SECRET_KEY: str = "default_development_secret_key_please_change_in_prod"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    CORS_ALLOW_ORIGINS: str = ""
    MAX_UPLOAD_SIZE_BYTES: int = Field(default=25 * 1024 * 1024, ge=1024)
    DB_RLS_ENABLED: bool = False

    # Chat trace & RAGAS: lưu query/answer/context snapshot để chấm điểm định kỳ.
    # Tắt bằng CHAT_HISTORY_ENABLED=false nếu không muốn lưu nội dung hội thoại.
    CHAT_HISTORY_ENABLED: bool = True
    CHAT_HISTORY_RETENTION_DAYS: int = Field(default=365, ge=1, le=365)

    # Database
    POSTGRES_SERVER: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "postgres_password"
    POSTGRES_DB: str = "saas_knowledge_db"

    @property
    def async_database_url(self) -> str:
        """Sinh chuỗi kết nối async SQLAlchemy (asyncpg)."""
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_SERVER}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    # FAISS Storage
    FAISS_INDEX_STORAGE_PATH: str = "backend/storage/indexes"

    # FAISS IVF-PQ: dưới ngưỡng dùng FlatIP (chính xác); vượt ngưỡng tự nâng cấp
    # sang IVF + Product Quantization để giảm hàng chục lần RAM/đĩa trên e2-micro.
    FAISS_IVF_MIN_VECTORS: int = Field(default=20000, ge=100)
    FAISS_PQ_M: int = Field(default=24, ge=1)
    FAISS_PQ_NBITS: int = Field(default=8, ge=4, le=16)
    FAISS_IVF_NLIST_MAX: int = Field(default=1024, ge=4)
    FAISS_IVF_NPROBE: int = Field(default=32, ge=1)
    FAISS_PQ_TRAIN_MAX: int = Field(default=30000, ge=1000)

    # Google AI Studio Unified Key (dùng chung cho cả Embedding lẫn LLM)
    GOOGLE_API_KEY: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("GOOGLE_API_KEY", "GEMINI_API_KEY"),
    )

    # Embedding Service (gemini-embedding-001, outputDimensionality=786, batch max 10 chunks/request)
    EMBEDDING_PROVIDER: str = "google"  # "google" | "openai_compatible"
    EMBEDDING_DIMENSION: int = 786
    EMBEDDING_MODEL: str = "gemini-embedding-001"
    EMBEDDING_BATCH_SIZE: int = Field(default=10, ge=1, le=10)
    EMBEDDING_MAX_CONCURRENCY: int = Field(default=3, ge=1, le=5)
    EMBEDDING_API_URL: Optional[str] = None
    EMBEDDING_API_KEY: Optional[str] = None

    # LLM Cloud Inference (Gemini 3.5 Flash Lite / OpenAI-compatible)
    LLM_PROVIDER: str = "google"  # "google" | "openai_compatible"
    LLM_MODEL: str = Field(
        default="gemini-3.5-flash-lite",
        validation_alias=AliasChoices("LLM_MODEL", "GEMMA_MODEL_NAME"),
    )
    LLM_API_URL: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("LLM_API_URL", "GEMMA_API_URL"),
    )
    LLM_API_KEY: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("LLM_API_KEY", "GEMMA_API_KEY"),
    )

    @property
    def effective_llm_api_key(self) -> Optional[str]:
        """Ưu tiên LLM_API_KEY nếu là key thật, ngược lại dùng chung GOOGLE_API_KEY."""
        if self.LLM_API_KEY and not self.LLM_API_KEY.startswith("your_"):
            return self.LLM_API_KEY
        return self.GOOGLE_API_KEY

    @property
    def effective_embedding_api_key(self) -> Optional[str]:
        """Ưu tiên EMBEDDING_API_KEY nếu là key thật, ngược lại dùng chung GOOGLE_API_KEY."""
        if self.EMBEDDING_API_KEY and not self.EMBEDDING_API_KEY.startswith("your_"):
            return self.EMBEDDING_API_KEY
        return self.GOOGLE_API_KEY

    @property
    def cors_origins(self) -> List[str]:
        """Danh sách origin được phép CORS; production bắt buộc cấu hình tường minh."""
        configured = [origin.strip() for origin in self.CORS_ALLOW_ORIGINS.split(",") if origin.strip()]
        if configured:
            return configured
        return ["*"] if self.DEBUG else ["https://yourdomain.com"]

    def validate_runtime_security(self) -> None:
        """
        Kiểm tra cấu hình bảo mật bắt buộc khi chạy production (Fail-Closed).
        Raises:
            RuntimeError nếu cấu hình nguy hiểm.
        """
        if self.ENVIRONMENT.lower() not in {"production", "prod"}:
            return
        problems: List[str] = []
        if self.DEBUG:
            problems.append("DEBUG phải bằng False trên production")
        weak_secret_markers = ("default_", "change_me", "change_this", "your_", "secret_key_here")
        if (
            len(self.SECRET_KEY) < 32
            or any(marker in self.SECRET_KEY.lower() for marker in weak_secret_markers)
        ):
            problems.append("SECRET_KEY phải được thay bằng chuỗi ngẫu nhiên tối thiểu 32 ký tự")
        if "*" in self.cors_origins:
            problems.append("CORS_ALLOW_ORIGINS không được chứa '*' trên production")
        if not self.DB_RLS_ENABLED:
            problems.append("DB_RLS_ENABLED phải bật trên production để áp Row-Level Security")
        if problems:
            raise RuntimeError("Cấu hình production không an toàn: " + "; ".join(problems))


settings = Settings()
