"""
agent.py – Core AI pipeline for Vinmec AI Triage system.

Pipeline stages (in order):
1. De-identification  : Presidio strips PII/PHI before any LLM call
2. Symptom extraction : LLM extracts core symptoms from noisy free-text
3. Red-flag check     : pgvector cosine similarity → EMERGENCY if > 0.85
4. LLM triage         : Map symptoms → department + confidence score
5. Clinical summary   : Short nurse-facing summary
6. DB persistence     : Insert triage_log + queue entry when needed
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

import psycopg2
import psycopg2.extras
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam, ChatCompletionToolParam

from .config import settings

logger = logging.getLogger("vinmec.agent")

# ---------------------------------------------------------------------------
# AGENT CONFIGURATION (Prompt & Tools)
# ---------------------------------------------------------------------------

_AGENT_SYSTEM_PROMPT = """Bạn là trợ lý AI Điều dưỡng Sơ yếu của Vinmec. Nhiệm vụ của bạn là thu thập triệu chứng và phân luồng bệnh nhân.
Quy tắc hoạt động (Agentic Loop):
1. LUÔN LUÔN gọi tool `check_emergency` đầu tiên để quét rủi ro.
2. Nếu thiếu thông tin, hãy trực tiếp hỏi lại bệnh nhân (không gọi tool).
3. Khi đủ thông tin, tự đánh giá độ tự tin (Confidence).
   - Nếu tự tin >= 85%: gọi tool `resolve_and_get_booking_info`.
   - Nếu tự tin < 85%: gọi tool `escalate_to_human_nurse`.
Tuyệt đối không chẩn đoán bệnh hay kê đơn thuốc. Giao tiếp bằng tiếng Việt tự nhiên."""

_AGENT_TOOLS: list[Any] = [
    {
        "type": "function",
        "function": {
            "name": "check_emergency",
            "description": "Kiểm tra các dấu hiệu cảnh báo đỏ (Cấp cứu). BẮT BUỘC gọi công cụ này ngay sau khi nhận được triệu chứng mới.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symptoms": {
                        "type": "string",
                        "description": "Triệu chứng cốt lõi của bệnh nhân",
                    }
                },
                "required": ["symptoms"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "escalate_to_human_nurse",
            "description": "Chuyển ca bệnh cho điều dưỡng thật khi không chắc chắn về chuyên khoa (độ tự tin < 85%) hoặc cần chẩn đoán phức tạp.",
            "parameters": {
                "type": "object",
                "properties": {
                    "clinical_summary": {
                        "type": "string",
                        "description": "Tóm tắt bệnh án ngắn gọn cho điều dưỡng",
                    },
                    "suggested_dept": {
                        "type": "string",
                        "description": "Mã chuyên khoa dự đoán (có thể null)",
                    },
                },
                "required": ["clinical_summary"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "resolve_and_get_booking_info",
            "description": "Gọi khi ĐÃ CHẮC CHẮN (>85%) về chuyên khoa. Lấy danh sách bác sĩ và cơ sở để bệnh nhân đặt lịch.",
            "parameters": {
                "type": "object",
                "properties": {
                    "department_code": {
                        "type": "string",
                        "description": "Mã khoa (VD: CARD, GAST, ...)",
                    },
                    "department_name": {
                        "type": "string",
                        "description": "Tên khoa bằng tiếng Việt",
                    },
                },
                "required": ["department_code", "department_name"],
            },
        },
    },
]

# ---------------------------------------------------------------------------
# Module-level singletons (initialised lazily to avoid import-time crashes)
# ---------------------------------------------------------------------------

_openai_client: AsyncOpenAI | None = None


def _get_openai() -> AsyncOpenAI:
    """Return (or create) the shared AsyncOpenAI client."""
    global _openai_client
    if _openai_client is None:
        _openai_client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    return _openai_client


# ---------------------------------------------------------------------------
# 1. De-identification (regex-based, no external dependencies)
# ---------------------------------------------------------------------------

# Compiled patterns for common PII found in Vietnamese healthcare text
_PII_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Vietnamese mobile numbers: 03x, 05x, 07x, 08x, 09x – both local (0) and
    # international (+84) prefixes.  The local form is 10 digits; the +84 form
    # drops the leading 0, yielding 11 chars total (e.g. +84912345678).
    (
        re.compile(r"(?:\+84|0)(3[2-9]|5[25689]|7[06-9]|8[0-9]|9[0-9])\d{7}"),
        "<SĐT>",
    ),
    # Vietnamese national ID / CCCD: exactly 9 or 12 digits (standalone)
    (re.compile(r"(?<!\d)\d{9}(?!\d)|(?<!\d)\d{12}(?!\d)"), "<CMND/CCCD>"),
    # Email addresses
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}\b"), "<EMAIL>"),
    # IPv4 addresses
    (re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"), "<IP>"),
    # URLs (http / https)
    (re.compile(r"https?://\S+"), "<URL>"),
    # Vietnamese full names: 2-4 words (capitalised Unicode) preceded by
    # common identity phrases like "tên tôi là", "họ tên", "bệnh nhân".
    # The last word in Vietnamese names is often a single uppercase letter
    # (e.g. "Nguyễn Văn A"), so we allow single-char final tokens.
    # We replace the ENTIRE match (keyword + name) with <TÊN_BN>.
    (
        re.compile(
            r"(?:"
            r"tên(?:\s+(?:tôi|em|mình|bé|con|là))?|"
            r"họ\s+(?:và\s+)?tên|"
            r"bệnh\s+nhân|"
            r"tôi\s+là|em\s+là|mình\s+là"
            r")\s*:?\s*"
            r"(?:"
            # First word: must have at least 2 chars
            r"[A-ZÀÁÂÃÈÉÊÌÍÒÓÔÕÙÚÝĂĐƠƯẠẢẤẦẨẪẬẮẰẲẴẶẸẺẼẾỀỂỄỆỈỊỌỎỐỒỔỖỘỚỜỞỠỢỤỦỨỪỬỮỰỲỴỶỸ]"
            r"[a-zàáâãèéêìíòóôõùúýăđơưạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ]+"
            r"(?:\s+"
            # Subsequent words: 1+ chars (handles single-letter given names like "A")
            r"[A-ZÀÁÂÃÈÉÊÌÍÒÓÔÕÙÚÝĂĐƠƯẠẢẤẦẨẪẬẮẰẲẴẶẸẺẼẾỀỂỄỆỈỊỌỎỐỒỔỖỘỚỜỞỠỢỤỦỨỪỬỮỰỲỴỶỸ]"
            r"[a-zàáâãèéêìíòóôõùúýăđơưạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ]*"
            r"){1,3}"
            r")",
            re.UNICODE,
        ),
        "<TÊN_BN>",  # replace the full match (keyword + name) with placeholder
    ),
]


def deidentify_text(text: str) -> str:
    """
    Strip PII/PHI from *text* using compiled regex patterns before sending
    to the LLM cloud.

    Covers: Vietnamese phone numbers, national IDs (CCCD/CMND), email
    addresses, IP addresses, URLs, and names introduced by common Vietnamese
    phrases (e.g. "tên tôi là Nguyễn Văn A").

    Parameters
    ----------
    text:
        Raw free-text from the patient.

    Returns
    -------
    str
        Anonymised text with PII replaced by labelled placeholders.
    """
    if not text or not text.strip():
        return text

    for pattern, replacement in _PII_PATTERNS:
        try:
            text = pattern.sub(replacement, text)
        except Exception as exc:  # noqa: BLE001
            logger.warning("PII regex substitution failed: %s", exc)

    return text


# ---------------------------------------------------------------------------
# 2. Embeddings
# ---------------------------------------------------------------------------


async def get_embedding(text: str) -> list[float]:
    """
    Generate a 1 536-dimensional embedding for *text* using
    ``text-embedding-3-small``.

    Parameters
    ----------
    text:
        The input string to embed.

    Returns
    -------
    list[float]
        A list of 1 536 floats representing the embedding vector.
    """
    client = _get_openai()
    response = await client.embeddings.create(
        model=settings.OPENAI_EMBEDDING_MODEL,
        input=text.replace("\n", " "),
        dimensions=settings.OPENAI_EMBEDDING_DIMS,
    )
    return response.data[0].embedding


# ---------------------------------------------------------------------------
# 3. Red-flag check
# ---------------------------------------------------------------------------


async def check_red_flags(
    symptoms_text: str,
    conn: Any,  # psycopg2 connection
) -> tuple[bool, str, float]:
    """
    Check whether *symptoms_text* semantically matches any red-flag keyword.

    Embeds the symptoms text and performs a pgvector cosine-similarity query
    against the ``red_flags`` table.  Returns ``True`` (emergency) if the
    top-1 similarity exceeds ``settings.RED_FLAG_SIMILARITY_THRESHOLD``.

    Parameters
    ----------
    symptoms_text:
        Cleaned symptom text (already de-identified).
    conn:
        Active psycopg2 connection to the Supabase/Postgres database.

    Returns
    -------
    tuple[bool, str, float]
        ``(is_emergency, matched_keyword, similarity_score)``
    """
    try:
        embedding = await get_embedding(symptoms_text)
        embedding_str = "[" + ",".join(str(x) for x in embedding) + "]"

        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(
                """
                SELECT keyword,
                       1 - (embedding <=> %s::vector) AS similarity
                FROM   red_flags
                ORDER  BY similarity DESC
                LIMIT  1
                """,
                (embedding_str,),
            )
            row = cur.fetchone()

        if row is None:
            logger.warning("red_flags table is empty – skipping red-flag check.")
            return False, "", 0.0

        keyword: str = row["keyword"]
        similarity: float = float(row["similarity"])

        logger.info(
            "Red-flag check: top match='%s' similarity=%.4f", keyword, similarity
        )

        if similarity >= settings.RED_FLAG_SIMILARITY_THRESHOLD:
            return True, keyword, similarity

        return False, keyword, similarity

    except Exception as exc:  # noqa: BLE001
        logger.error("Red-flag check failed: %s", exc, exc_info=True)
        # Fail open – do NOT trigger emergency on DB/embedding error
        return False, "", 0.0


# ---------------------------------------------------------------------------
# 4. LLM symptom extraction
# ---------------------------------------------------------------------------

_EXTRACTION_SYSTEM_PROMPT = """\
Bạn là trợ lý y tế. Nhiệm vụ DUY NHẤT của bạn là trích xuất các triệu chứng \
lâm sàng thuần túy từ đoạn văn bản của bệnh nhân.

Quy tắc:
- Loại bỏ tên, số điện thoại, địa chỉ và mọi thông tin nhận dạng cá nhân.
- Giữ lại thông tin về: triệu chứng, thời gian xuất hiện, mức độ nghiêm trọng, \
tuổi/giới tính nếu có.
- Trả về JSON duy nhất: \
{"symptoms": "mô tả triệu chứng ngắn gọn", "age": số hoặc null, "gender": "nam"/"nữ"/null}
- Không thêm bất kỳ văn bản nào ngoài JSON."""


async def extract_symptoms(raw_text: str) -> dict[str, Any]:
    """
    Use the LLM to extract structured symptom information from noisy free-text.

    Parameters
    ----------
    raw_text:
        De-identified patient input.

    Returns
    -------
    dict
        ``{"symptoms": str, "age": int | None, "gender": str | None}``
    """
    client = _get_openai()

    response = await client.chat.completions.create(
        model=settings.OPENAI_CHAT_MODEL,
        messages=[
            {"role": "system", "content": _EXTRACTION_SYSTEM_PROMPT},
            {"role": "user", "content": raw_text},
        ],
        temperature=0.1,
        max_tokens=300,
        response_format={"type": "json_object"},
    )

    content = response.choices[0].message.content or "{}"
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        logger.warning("Symptom extraction: JSON parse failed; returning raw text.")
        parsed = {"symptoms": raw_text, "age": None, "gender": None}

    # Ensure all expected keys are present
    parsed.setdefault("symptoms", raw_text)
    parsed.setdefault("age", None)
    parsed.setdefault("gender", None)

    return parsed


# ---------------------------------------------------------------------------
# 5. LLM triage routing
# ---------------------------------------------------------------------------


async def triage_symptoms(
    symptoms_text: str,
    conversation_history: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """
    Call the LLM triage router to map symptoms to a department.

    Parameters
    ----------
    symptoms_text:
        Core symptom description (already extracted and de-identified).
    conversation_history:
        Optional prior turns in the session for multi-turn context.

    Returns
    -------
    dict
        Parsed JSON from the LLM with keys:
        ``department_code``, ``department_name``, ``confidence_score``,
        ``follow_up_question`` (may be ``None``), ``clinical_summary``.
    """
    client = _get_openai()

    from openai.types.chat import ChatCompletionMessageParam

    messages: list[ChatCompletionMessageParam] = [
        {"role": "system", "content": settings.TRIAGE_SYSTEM_PROMPT},
    ]

    # Inject previous conversation turns for multi-turn awareness
    if conversation_history:
        for turn in conversation_history:
            role = turn.get("role", "user")
            content = turn.get("content", "")
            if role in ("user", "assistant", "system"):
                messages.append({"role": role, "content": content})  # type: ignore[arg-type]

    messages.append({"role": "user", "content": symptoms_text})

    response = await client.chat.completions.create(
        model=settings.OPENAI_CHAT_MODEL,
        messages=messages,
        temperature=0.2,  # Low temperature for consistent routing
        max_tokens=512,
        response_format={"type": "json_object"},
    )

    content = response.choices[0].message.content or "{}"

    try:
        result = json.loads(content)
    except json.JSONDecodeError as exc:
        logger.error("Triage LLM returned invalid JSON: %s | raw=%s", exc, content)
        # Graceful degradation: send to human queue
        result = {
            "department_code": None,
            "department_name": None,
            "confidence_score": 0,
            "follow_up_question": "Xin lỗi, hệ thống gặp sự cố. Điều dưỡng sẽ hỗ trợ bạn.",
            "clinical_summary": f"Lỗi phân tích tự động. Triệu chứng gốc: {symptoms_text[:200]}",
        }

    # Normalise types
    try:
        raw_confidence = result.get("confidence_score", 0)
        result["confidence_score"] = (
            int(raw_confidence) if raw_confidence is not None else 0
        )
    except (TypeError, ValueError):
        result["confidence_score"] = 0

    result.setdefault("department_code", None)
    result.setdefault("department_name", None)
    result.setdefault("follow_up_question", None)
    result.setdefault("clinical_summary", "")

    return result


# ---------------------------------------------------------------------------
# 6. Clinical summary generation
# ---------------------------------------------------------------------------


async def generate_clinical_summary(
    symptoms: str,
    triage_result: dict[str, Any],
    age: int | None = None,
    gender: str | None = None,
) -> str:
    """
    Generate a concise clinical summary in Vietnamese for the nurse dashboard.

    This is a lightweight LLM call (≤ 120 tokens) so it adds minimal latency.

    Parameters
    ----------
    symptoms:
        Core symptom description.
    triage_result:
        The JSON dict returned by :func:`triage_symptoms`.
    age:
        Patient age (optional).
    gender:
        Patient gender string (optional).

    Returns
    -------
    str
        2–3 sentence Vietnamese clinical summary.
    """
    # If the LLM already provided a summary, use it directly
    existing_summary: str = triage_result.get("clinical_summary", "").strip()
    if existing_summary and len(existing_summary) > 20:
        return existing_summary

    client = _get_openai()

    demographics = ""
    if age:
        demographics += f"Tuổi: {age}. "
    if gender:
        demographics += f"Giới tính: {gender}. "

    prompt = (
        f"{demographics}Triệu chứng: {symptoms}\n"
        f"Khoa đề xuất: {triage_result.get('department_name', 'Chưa xác định')} "
        f"(confidence: {triage_result.get('confidence_score', 0)}%).\n\n"
        "Viết tóm tắt lâm sàng ngắn gọn (2-3 câu) bằng tiếng Việt cho điều dưỡng. "
        "Không chẩn đoán, không kê đơn. Chỉ mô tả triệu chứng và lý do điều phối."
    )

    try:
        response = await client.chat.completions.create(
            model=settings.OPENAI_CHAT_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=150,
        )
        return (response.choices[0].message.content or "").strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Clinical summary generation failed: %s", exc)
        dept = triage_result.get("department_name", "chưa xác định")
        return (
            f"Bệnh nhân có triệu chứng: {symptoms[:120]}. "
            f"AI đề xuất điều phối đến {dept} "
            f"(độ tin cậy: {triage_result.get('confidence_score', 0)}%)."
        )


# ---------------------------------------------------------------------------
# 7. Database helpers
# ---------------------------------------------------------------------------


def _get_db_connection() -> Any:
    """
    Open a new psycopg2 connection using ``settings.DATABASE_URL``.

    Uses ``psycopg2.extras.register_uuid()`` so UUID objects are handled
    natively and ``register_default_jsonb`` for any JSONB columns.
    """
    psycopg2.extras.register_uuid()
    conn = psycopg2.connect(settings.DATABASE_URL)
    conn.autocommit = False
    return conn


@asynccontextmanager
async def db_connection() -> AsyncGenerator[Any, None]:
    """
    Async context manager that yields a psycopg2 connection and handles
    commit / rollback / close automatically.

    Usage::

        async with db_connection() as conn:
            do_something(conn)
    """
    conn = None
    try:
        conn = _get_db_connection()
        yield conn
        conn.commit()
    except Exception:
        if conn is not None:
            conn.rollback()
        raise
    finally:
        if conn is not None:
            conn.close()


async def insert_triage_log(
    conn: Any,
    raw_symptoms: str,
    symptom_embedding: list[float],
    ai_suggested_dept: str | None,
    confidence: float,
) -> str:
    """
    Insert a row into ``triage_logs`` and return the new UUID string.

    The ``final_dept`` and ``resolution_type`` columns are left NULL here;
    they are filled in later by :func:`resolve_queue_item`.
    """
    log_id = str(uuid.uuid4())
    embedding_str = "[" + ",".join(str(x) for x in symptom_embedding) + "]"

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO triage_logs
                (id, raw_symptoms, symptom_embedding, ai_suggested_dept, confidence)
            VALUES (%s, %s, %s::vector, %s, %s)
            """,
            (log_id, raw_symptoms, embedding_str, ai_suggested_dept, confidence),
        )
    return log_id


async def insert_to_queue(
    conn: Any,
    patient_id: str,
    clinical_summary: str,
    suggested_dept: str | None,
) -> str:
    """
    Insert a new ``human_triage_queue`` entry and return its UUID string.

    Parameters
    ----------
    conn:
        Active psycopg2 connection (caller handles commit).
    patient_id:
        Opaque patient identifier.
    clinical_summary:
        Nurse-facing summary text.
    suggested_dept:
        Department code the AI proposed (may be ``None``).

    Returns
    -------
    str
        UUID string of the newly created queue entry.
    """
    queue_id = str(uuid.uuid4())

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO human_triage_queue
                (id, patient_id, clinical_summary, suggested_dept, status)
            VALUES (%s, %s, %s, %s, 'PENDING')
            """,
            (queue_id, patient_id, clinical_summary, suggested_dept),
        )

    logger.info("Inserted queue entry %s for patient %s", queue_id, patient_id)
    return queue_id


async def resolve_queue_item(
    conn: Any,
    queue_id: str,
    approved_dept: str,
    resolution_type: str,
) -> bool:
    """
    Mark a ``human_triage_queue`` entry as RESOLVED and back-fill
    ``triage_logs`` with the nurse's final decision.

    Parameters
    ----------
    conn:
        Active psycopg2 connection.
    queue_id:
        UUID of the queue entry to resolve.
    approved_dept:
        The department code chosen by the nurse.
    resolution_type:
        One of ``NURSE_APPROVED`` / ``NURSE_CORRECTED``.

    Returns
    -------
    bool
        ``True`` if a row was updated, ``False`` if the queue entry was not found.
    """
    with conn.cursor() as cur:
        # Mark queue item resolved
        cur.execute(
            """
            UPDATE human_triage_queue
            SET    status = 'RESOLVED'
            WHERE  id = %s AND status = 'PENDING'
            RETURNING id
            """,
            (queue_id,),
        )
        updated = cur.fetchone()

        if not updated:
            return False

        # Back-fill triage_logs – match on the most recent log for this patient
        # (We join via suggested_dept as a best-effort since we don't store
        #  queue_id in triage_logs to keep the schema unchanged.)
        cur.execute(
            """
            UPDATE triage_logs
            SET    final_dept       = %s,
                   resolution_type  = %s::triage_resolution
            WHERE  id = (
                SELECT tl.id
                FROM   triage_logs tl
                WHERE  tl.ai_suggested_dept = (
                    SELECT suggested_dept
                    FROM   human_triage_queue
                    WHERE  id = %s
                )
                ORDER  BY tl.created_at DESC
                LIMIT  1
            )
            """,
            (approved_dept, resolution_type, queue_id),
        )

    return True


async def get_pending_queue(conn: Any) -> list[dict[str, Any]]:
    """
    Fetch all ``PENDING`` entries from ``human_triage_queue``,
    ordered oldest-first (highest SLA urgency first).
    """
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(
            """
            SELECT id, patient_id, clinical_summary, suggested_dept,
                   status, created_at
            FROM   human_triage_queue
            WHERE  status = 'PENDING'
            ORDER  BY created_at ASC
            """
        )
        rows = cur.fetchall()

    return [dict(row) for row in rows]


async def mark_timed_out_items(conn: Any, sla_minutes: int) -> int:
    """
    Mark all ``PENDING`` items older than *sla_minutes* as ``TIMEOUT``.

    Returns
    -------
    int
        Number of rows updated.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE human_triage_queue
            SET    status = 'TIMEOUT'
            WHERE  status = 'PENDING'
              AND  created_at < NOW() - (%s || ' minutes')::INTERVAL
            """,
            (str(sla_minutes),),
        )
        count: int = cur.rowcount

    logger.info("SLA timeout sweep: marked %d items as TIMEOUT", count)
    return count


async def seed_red_flags(conn: Any, keywords: list[str]) -> int:
    """
    Generate OpenAI embeddings for each keyword and upsert into ``red_flags``.

    Uses ``INSERT … ON CONFLICT (keyword) DO UPDATE`` so re-running the
    endpoint is idempotent.

    Returns
    -------
    int
        Number of rows inserted/updated.
    """
    count = 0
    for keyword in keywords:
        try:
            embedding = await get_embedding(keyword)
            embedding_str = "[" + ",".join(str(x) for x in embedding) + "]"

            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO red_flags (keyword, embedding)
                    VALUES (%s, %s::vector)
                    ON CONFLICT (keyword)
                    DO UPDATE SET embedding = EXCLUDED.embedding
                    """,
                    (keyword, embedding_str),
                )
            count += 1
            logger.info("Seeded red-flag keyword: '%s'", keyword)
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to seed red-flag '%s': %s", keyword, exc)

    conn.commit()
    return count


# ---------------------------------------------------------------------------
# 7b. Doctor / clinic / appointment helpers
# ---------------------------------------------------------------------------


async def get_doctors_by_department(
    conn: Any,
    department_code: str,
) -> list[dict[str, Any]]:
    """
    Fetch up to 5 doctors for the given *department_code*.

    Returns
    -------
    list[dict]
        Each dict has keys: ``id``, ``name``, ``specialty``, ``department_code``.
    """
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(
                """
                SELECT id::text, name, specialty, department_code
                FROM   doctors
                WHERE  department_code = %s
                ORDER  BY name
                LIMIT  5
                """,
                (department_code,),
            )
            rows = cur.fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_doctors_by_department failed: %s", exc)
        return []


async def get_clinics_by_department(
    conn: Any,
    department_code: str,
) -> list[dict[str, Any]]:
    """
    Fetch all clinics that serve *department_code* (across all branches).

    Returns
    -------
    list[dict]
        Each dict has keys ``name``, ``address``.
    """
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(
                """
                SELECT name, address
                FROM   clinics
                WHERE  department_code = %s
                ORDER  BY name
                """,
                (department_code,),
            )
            rows = cur.fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_clinics_by_department failed: %s", exc)
        return []


async def create_appointment(
    conn: Any,
    patient_id: str,
    doctor_id: str,
    department_code: str,
    appointment_time: str,
) -> str:
    """
    Insert a new appointment row and return its UUID string.

    Parameters
    ----------
    appointment_time:
        ISO 8601 string (e.g. ``"2026-04-10T08:00:00+07:00"``).
    """
    appt_id = str(uuid.uuid4())
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO appointments
                (id, patient_id, doctor_id, department_code, appointment_time)
            VALUES (%s, %s, %s::uuid, %s, %s::timestamptz)
            """,
            (appt_id, patient_id, doctor_id, department_code, appointment_time),
        )
    logger.info(
        "Appointment created: id=%s patient=%s doctor=%s",
        appt_id,
        patient_id,
        doctor_id,
    )
    return appt_id


# ---------------------------------------------------------------------------
# 8. Main orchestration pipeline
# ---------------------------------------------------------------------------


async def run_triage_pipeline(
    patient_id: str,
    message: str,
    conversation_history: list[dict[str, str]] | None = None,
    follow_up_rounds: int = 0,  # Kept for compatibility with api.py
) -> dict[str, Any]:

    clean_text = deidentify_text(message)
    client = _get_openai()

    messages: list[ChatCompletionMessageParam] = [
        {"role": "system", "content": _AGENT_SYSTEM_PROMPT}
    ]

    if conversation_history:
        for turn in conversation_history:
            # Only add standard roles
            if turn.get("role") in ["user", "assistant", "system", "tool"]:
                messages.append(
                    {"role": turn.get("role"), "content": turn.get("content", "")}
                )  # type: ignore

    messages.append({"role": "user", "content": clean_text})

    conn = None
    try:
        conn = _get_db_connection()
    except Exception as exc:
        logger.warning("DB unavailable (%s); proceeding with caution.", exc)

    result: dict[str, Any] = {
        "flow": "FOLLOW_UP",
        "department_code": None,
        "department_name": None,
        "confidence_score": None,
        "patient_message": None,
        "queue_id": None,
        "doctors": None,
        "clinics": None,
    }

    # Vòng lặp tự trị (Tối đa 3 steps để tránh infinite loop)
    MAX_ITERATIONS = 3
    for _ in range(MAX_ITERATIONS):
        response = await client.chat.completions.create(
            model=settings.OPENAI_CHAT_MODEL,
            messages=messages,
            tools=_AGENT_TOOLS,
            tool_choice="auto",
        )

        response_message = response.choices[0].message
        messages.append(response_message)  # type: ignore

        if not response_message.tool_calls:
            # Agent quyết định giao tiếp trực tiếp với user (Follow-up)
            result["flow"] = "FOLLOW_UP"
            result["patient_message"] = response_message.content
            break

        # Agent quyết định sử dụng Tool
        tool_calls = response_message.tool_calls
        for tool_call in tool_calls:
            function_name = tool_call.function.name
            args = json.loads(tool_call.function.arguments)

            tool_result = ""

            if function_name == "check_emergency":
                if conn:
                    is_emergency, keyword, sim = await check_red_flags(
                        args["symptoms"], conn
                    )
                    if is_emergency:
                        result["flow"] = "EMERGENCY"
                        result["matched_keyword"] = keyword
                        result["similarity_score"] = sim
                        return result  # Ngắt ngay lập tức
                    tool_result = "No emergency detected. Safe to proceed."
                else:
                    tool_result = "DB unavailable, proceed with caution."

            elif function_name == "escalate_to_human_nurse":
                result["flow"] = "PENDING_HUMAN"
                if conn:
                    result["queue_id"] = await insert_to_queue(
                        conn,
                        patient_id,
                        args["clinical_summary"],
                        args.get("suggested_dept"),
                    )
                    conn.commit()
                tool_result = "Escalated successfully."
                result["patient_message"] = (
                    "Hệ thống đã ghi nhận triệu chứng. Tôi đang chuyển hồ sơ của bạn cho điều dưỡng chuyên môn để hỗ trợ trực tiếp."
                )
                return result

            elif function_name == "resolve_and_get_booking_info":
                result["flow"] = "AUTO_RESOLVED"
                result["department_code"] = args["department_code"]
                result["department_name"] = args["department_name"]
                if conn:
                    result["doctors"] = await get_doctors_by_department(
                        conn, args["department_code"]
                    )
                    result["clinics"] = await get_clinics_by_department(
                        conn, args["department_code"]
                    )
                tool_result = "Booking info retrieved."
                result["patient_message"] = (
                    f"Tôi khuyên bạn nên khám tại khoa {args['department_name']}. Dưới đây là các bác sĩ phù hợp."
                )
                return result

            # Feed kết quả tool lại cho Agent
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "name": function_name,
                    "content": tool_result,
                }
            )

    if conn:
        conn.close()

    return result
