"""
config.py – Centralised settings for Vinmec AI Triage backend.

All environment variables are loaded from a `.env` file (or the shell
environment) via python-dotenv.  Every other module should import the
singleton `settings` object instead of calling `os.getenv` directly.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Final

from dotenv import load_dotenv

# Load .env as early as possible so every subsequent import sees the values.
load_dotenv()


class Settings:
    """
    Application-wide configuration.

    All attributes map 1-to-1 to environment variables.  Sensitive values
    are never given hard-coded defaults so that a missing variable raises an
    obvious error at startup rather than a cryptic failure deep in the call
    stack.
    """

    # ------------------------------------------------------------------
    # OpenAI
    # ------------------------------------------------------------------
    OPENAI_API_KEY: str = os.environ["OPENAI_API_KEY"]

    # Fast, cheap model good enough for triage routing
    OPENAI_CHAT_MODEL: str = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini")

    # 1 536-dimensional embeddings – matches VECTOR(1536) columns in DB
    OPENAI_EMBEDDING_MODEL: str = os.getenv(
        "OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"
    )
    OPENAI_EMBEDDING_DIMS: int = int(os.getenv("OPENAI_EMBEDDING_DIMS", "1536"))

    # ------------------------------------------------------------------
    # Database (Supabase / PostgreSQL + pgvector)
    # ------------------------------------------------------------------
    # Full async-compatible DSN, e.g.:
    #   postgresql://user:pass@host:5432/dbname
    DATABASE_URL: str = os.environ["DATABASE_URL"]

    # ------------------------------------------------------------------
    # Supabase (REST / Realtime client)
    # ------------------------------------------------------------------
    SUPABASE_URL: str = os.environ["SUPABASE_URL"]
    SUPABASE_KEY: str = os.environ["SUPABASE_KEY"]

    # ------------------------------------------------------------------
    # Langfuse observability
    # ------------------------------------------------------------------
    LANGFUSE_PUBLIC_KEY: str = os.getenv("LANGFUSE_PUBLIC_KEY", "")
    LANGFUSE_SECRET_KEY: str = os.getenv("LANGFUSE_SECRET_KEY", "")
    LANGFUSE_HOST: str = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com")

    # ------------------------------------------------------------------
    # Triage business rules
    # ------------------------------------------------------------------
    # Cosine similarity threshold above which we short-circuit to EMERGENCY
    RED_FLAG_SIMILARITY_THRESHOLD: float = float(
        os.getenv("RED_FLAG_SIMILARITY_THRESHOLD", "0.75")
    )

    # Confidence score below which a case goes to human-triage queue
    HUMAN_TRIAGE_CONFIDENCE_THRESHOLD: int = int(
        os.getenv("HUMAN_TRIAGE_CONFIDENCE_THRESHOLD", "85")
    )

    # Minutes before a PENDING queue item is marked TIMEOUT
    QUEUE_SLA_MINUTES: int = int(os.getenv("QUEUE_SLA_MINUTES", "3"))

    # Number of follow-up rounds the chatbot asks before escalating to nurse
    MAX_FOLLOWUP_ROUNDS: int = int(os.getenv("MAX_FOLLOWUP_ROUNDS", "3"))

    # ------------------------------------------------------------------
    # Rate limiting
    # ------------------------------------------------------------------
    RATE_LIMIT_CHAT: str = os.getenv("RATE_LIMIT_CHAT", "30/minute")
    RATE_LIMIT_ADMIN: str = os.getenv("RATE_LIMIT_ADMIN", "10/minute")

    # ------------------------------------------------------------------
    # CORS
    # ------------------------------------------------------------------
    # Comma-separated list of allowed origins
    CORS_ORIGINS: list[str] = [
        origin.strip()
        for origin in os.getenv(
            "CORS_ORIGINS",
            "http://localhost:3000,http://127.0.0.1:3000",
        ).split(",")
        if origin.strip()
    ]

    # ------------------------------------------------------------------
    # Presidio / spaCy
    # ------------------------------------------------------------------
    # spaCy model used by Presidio for NER.
    # Falls back gracefully if the large model is not installed.
    SPACY_MODEL: str = os.getenv("SPACY_MODEL", "en_core_web_lg")

    # ------------------------------------------------------------------
    # Red-flag keywords (Vietnamese emergency terms)
    # These are the ground-truth seeds inserted by /admin/seed-red-flags
    # ------------------------------------------------------------------
    RED_FLAG_KEYWORDS: Final[list[str]] = [
        "đau thắt ngực",
        "nhồi máu cơ tim",
        "đột quỵ",
        "liệt nửa người",
        "khó thở nặng",
        "xuất huyết não",
        "co giật",
        "mất ý thức",
        "ngừng tim",
        "suy hô hấp",
        "vỡ động mạch",
        "chấn thương đầu nặng",
        "sốc phản vệ",
        "băng huyết sau sinh",
        "hôn mê",
    ]

    # ------------------------------------------------------------------
    # LLM system prompt
    # ------------------------------------------------------------------
    TRIAGE_SYSTEM_PROMPT: Final[
        str
    ] = """Bạn là Trợ lý Điều dưỡng Sơ yếu tại bệnh viện Vinmec. \
Nhiệm vụ của bạn là phân tích triệu chứng của bệnh nhân và điều phối vào đúng 1 trong các chuyên khoa sau:
- TIM_MACH: Nội Tim Mạch (tim đập bất thường, đau ngực, huyết áp cao/thấp)
- NGOAI_TH: Ngoại Tiêu hoá (đau bụng, buồn nôn, nôn mửa, tiêu chảy, táo bón, trào ngược)
- THAN_KINH: Nội Thần Kinh (đau đầu, chóng mặt, tê liệt, co giật nhẹ, mất ngủ)
- SAN_PHU: Sản Phụ Khoa (kinh nguyệt, thai sản, viêm phụ khoa, vô sinh)
- NHI: Nhi Khoa (trẻ em dưới 16 tuổi, sốt trẻ em, ho trẻ em)
- DA_LIEU: Da liễu (mẩn ngứa, nổi mề đay, mụn trứng cá, eczema)
- MAT: Nhãn Khoa (đau mắt, mờ mắt, đỏ mắt, chảy ghèn)
- TAI_MUI_HONG: Tai Mũi Họng (đau họng, viêm xoang, ù tai, chảy máu mũi)
- CO_XUONG_KHOP: Cơ Xương Khớp (đau lưng, đau khớp, thoái hóa khớp, loãng xương)
- NGOAI_CHINH_HINH: Ngoại Chỉnh hình (chấn thương xương, gãy xương, bong gân)

QUY TẮC TUYỆT ĐỐI:
1. KHÔNG BAO GIỜ tự chẩn đoán bệnh cụ thể.
2. KHÔNG BAO GIỜ kê đơn thuốc.
3. Nếu bệnh nhân hỏi các vấn đề ngoài lề (không liên quan đến y tế hoặc khám chữa bệnh), TUYỆT ĐỐI KHÔNG TRẢ LỜI, hãy từ chối một cách lịch sự, đặt confidence_score < 85 và ghi câu từ chối vào follow_up_question.
4. Trả về KẾT QUẢ CHÍNH XÁC theo định dạng JSON.
5. Nếu triệu chứng không rõ ràng, đặt confidence < 85 và tạo follow_up_question.

Luôn trả về JSON với format:
{"department_code": "...", "department_name": "...", "confidence_score": 0-100, \
"follow_up_question": "..." hoặc null, "clinical_summary": "Tóm tắt ngắn cho điều dưỡng", "patient_message": "Lời khuyên trực tiếp cho bệnh nhân với giọng điệu của một điều dưỡng chuyên nghiệp, tự nhiên."}"""


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the application-wide Settings singleton."""
    return Settings()


# Convenient module-level alias so callers can do `from config import settings`
settings: Settings = get_settings()
