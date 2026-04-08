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

from .config import settings

logger = logging.getLogger("vinmec.agent")

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
# 8. Main orchestration pipeline
# ---------------------------------------------------------------------------


async def run_triage_pipeline(
    patient_id: str,
    message: str,
    conversation_history: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """
    Orchestrate the full triage pipeline for a single patient message.

    Steps
    -----
    1. De-identify raw message with Presidio.
    2. Extract core symptoms via LLM.
    3. Check red-flag similarity via pgvector (short-circuit if emergency).
    4. Run LLM triage router to get department + confidence.
    5. Generate clinical summary.
    6. Persist triage_log and (if needed) human_triage_queue.
    7. Return a structured result dict consumed by the API layer.

    Parameters
    ----------
    patient_id:
        Opaque patient identifier (e.g. ``"PAT-00123"``).
    message:
        Raw free-text from the patient's chat input.
    conversation_history:
        Previous conversation turns for multi-turn context.

    Returns
    -------
    dict with keys:
        - ``flow``            : ``"AUTO_RESOLVED"`` | ``"PENDING_HUMAN"`` | ``"EMERGENCY"``
        - ``department_code`` : str | None
        - ``department_name`` : str | None
        - ``confidence_score``: int | None
        - ``follow_up_question``: str | None
        - ``clinical_summary``: str | None
        - ``queue_id``        : str | None  (set when flow == PENDING_HUMAN)
        - ``matched_keyword`` : str | None  (set when flow == EMERGENCY)
        - ``similarity_score``: float | None (set when flow == EMERGENCY)
        - ``error``           : str | None  (set on unexpected failures)
    """
    result: dict[str, Any] = {
        "flow": "PENDING_HUMAN",  # Safe default
        "department_code": None,
        "department_name": None,
        "confidence_score": None,
        "follow_up_question": None,
        "clinical_summary": None,
        "queue_id": None,
        "matched_keyword": None,
        "similarity_score": None,
        "error": None,
    }

    # ------------------------------------------------------------------
    # Step 1: De-identification
    # ------------------------------------------------------------------
    try:
        clean_text = deidentify_text(message)
        logger.debug("De-identified text: %s", clean_text[:120])
    except Exception as exc:  # noqa: BLE001
        logger.error("De-identification failed: %s", exc)
        clean_text = message  # Proceed with original on failure

    # ------------------------------------------------------------------
    # Step 2: Symptom extraction
    # ------------------------------------------------------------------
    try:
        extraction = await extract_symptoms(clean_text)
        symptoms_text: str = extraction.get("symptoms") or clean_text
        age: int | None = extraction.get("age")
        gender: str | None = extraction.get("gender")
    except Exception as exc:  # noqa: BLE001
        logger.error("Symptom extraction failed: %s", exc)
        symptoms_text = clean_text
        age = None
        gender = None

    # ------------------------------------------------------------------
    # Steps 3-6: Attempt DB-dependent operations
    # ------------------------------------------------------------------
    conn = None
    db_available = False

    try:
        conn = _get_db_connection()
        db_available = True
    except Exception as exc:  # noqa: BLE001
        logger.warning("DB unavailable (%s); skipping red-flags & persistence.", exc)

    try:
        # ------------------------------------------------------------------
        # Step 3: Red-flag check (only if DB is available)
        # ------------------------------------------------------------------
        if db_available and conn is not None:
            is_emergency, matched_keyword, similarity = await check_red_flags(
                symptoms_text, conn
            )

            if is_emergency:
                result["flow"] = "EMERGENCY"
                result["matched_keyword"] = matched_keyword
                result["similarity_score"] = similarity
                logger.warning(
                    "EMERGENCY triggered: keyword='%s' similarity=%.4f",
                    matched_keyword,
                    similarity,
                )
                # Do NOT call the triage LLM – return immediately
                return result

        # ------------------------------------------------------------------
        # Step 4: LLM triage routing
        # ------------------------------------------------------------------
        triage_result = await triage_symptoms(symptoms_text, conversation_history)

        confidence: int = triage_result.get("confidence_score", 0)
        dept_code: str | None = triage_result.get("department_code")
        dept_name: str | None = triage_result.get("department_name")
        follow_up: str | None = triage_result.get("follow_up_question")

        result["department_code"] = dept_code
        result["department_name"] = dept_name
        result["confidence_score"] = confidence
        result["follow_up_question"] = follow_up

        # ------------------------------------------------------------------
        # Step 5: Clinical summary
        # ------------------------------------------------------------------
        clinical_summary = await generate_clinical_summary(
            symptoms_text, triage_result, age, gender
        )
        result["clinical_summary"] = clinical_summary

        # ------------------------------------------------------------------
        # Step 6: Persist to DB
        # ------------------------------------------------------------------
        if db_available and conn is not None:
            try:
                # Always embed and log the triage attempt
                symptom_embedding = await get_embedding(symptoms_text)

                await insert_triage_log(
                    conn=conn,
                    raw_symptoms=symptoms_text,
                    symptom_embedding=symptom_embedding,
                    ai_suggested_dept=dept_code,
                    confidence=float(confidence),
                )

                # Queue for human review if confidence is below threshold
                if confidence < settings.HUMAN_TRIAGE_CONFIDENCE_THRESHOLD:
                    queue_id = await insert_to_queue(
                        conn=conn,
                        patient_id=patient_id,
                        clinical_summary=clinical_summary,
                        suggested_dept=dept_code,
                    )
                    result["queue_id"] = queue_id
                    result["flow"] = "PENDING_HUMAN"
                    logger.info(
                        "Low confidence (%d) → queued as %s", confidence, queue_id
                    )
                else:
                    result["flow"] = "AUTO_RESOLVED"

                conn.commit()

            except Exception as exc:  # noqa: BLE001
                logger.error("DB persistence failed: %s", exc, exc_info=True)
                if conn:
                    conn.rollback()
                # Fall back to confidence-based flow decision without DB
                if confidence >= settings.HUMAN_TRIAGE_CONFIDENCE_THRESHOLD:
                    result["flow"] = "AUTO_RESOLVED"
                else:
                    result["flow"] = "PENDING_HUMAN"

        else:
            # No DB – decide flow by confidence alone
            if confidence >= settings.HUMAN_TRIAGE_CONFIDENCE_THRESHOLD:
                result["flow"] = "AUTO_RESOLVED"
            else:
                result["flow"] = "PENDING_HUMAN"

    except Exception as exc:  # noqa: BLE001
        logger.error("Triage pipeline unexpected error: %s", exc, exc_info=True)
        result["error"] = str(exc)
        result["flow"] = "PENDING_HUMAN"

    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    return result
