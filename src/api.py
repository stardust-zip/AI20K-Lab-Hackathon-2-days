"""
api.py – FastAPI application for Vinmec AI Triage system.

Endpoints
---------
GET  /                              – Root info
GET  /health                        – Health check (DB ping + system metrics)
POST /api/v1/chat/triage            – Main patient triage chat endpoint
GET  /api/v1/queue/pending          – Nurse dashboard: list pending queue items
POST /api/v1/queue/resolve          – Nurse resolves / approves a queue item
POST /api/v1/queue/check-timeouts   – SLA sweep: mark stale PENDING items TIMEOUT
POST /api/v1/admin/seed-red-flags   – Seed red-flag embeddings into DB (one-time)
"""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import psutil
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from langfuse import get_client, observe
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlalchemy import create_engine, text

from .agent import (
    db_connection,
    get_pending_queue,
    mark_timed_out_items,
    resolve_queue_item,
    run_triage_pipeline,
    seed_red_flags,
)
from .config import settings
from .schema import (
    ChatRequest,
    ChatResponse,
    EmergencyResult,
    ErrorResponse,
    PendingQueueResponse,
    QueueItem,
    QueueStatus,
    ResolutionType,
    ResolveRequest,
    ResolveResponse,
    SeedRedFlagsResponse,
    TimeoutCheckResponse,
    TriageFlow,
    TriageResult,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("vinmec.api")

# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------

limiter = Limiter(key_func=get_remote_address, default_limits=["200/minute"])

# ---------------------------------------------------------------------------
# App startup / shutdown lifecycle
# ---------------------------------------------------------------------------

START_TIME = time.time()


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    """
    Application lifespan handler.

    On startup: log configuration summary.
    On shutdown: flush Langfuse traces.
    """
    logger.info("=== Vinmec AI Triage API starting ===")
    logger.info("Chat model  : %s", settings.OPENAI_CHAT_MODEL)
    logger.info("Embed model : %s", settings.OPENAI_EMBEDDING_MODEL)
    logger.info("CORS origins: %s", settings.CORS_ORIGINS)
    logger.info("Red-flag threshold : %.2f", settings.RED_FLAG_SIMILARITY_THRESHOLD)
    logger.info(
        "Human-triage threshold : %d", settings.HUMAN_TRIAGE_CONFIDENCE_THRESHOLD
    )
    yield
    # Flush any pending Langfuse events before the process exits
    try:
        lf = get_client()
        lf.flush()
        logger.info("Langfuse traces flushed.")
    except Exception:  # noqa: BLE001
        pass
    logger.info("=== Vinmec AI Triage API stopped ===")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Vinmec AI Triage API",
    description=(
        "AI-powered patient triage system for Vinmec hospital. "
        "De-identifies PII, detects red-flag emergencies via semantic similarity, "
        "routes symptoms to the appropriate department, and queues low-confidence "
        "cases for nurse review."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

# CORS – allow the Next.js frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rate limiting
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    """Attach X-Process-Time header and emit a structured access-log line."""
    start = time.time()
    response = await call_next(request)
    elapsed = time.time() - start
    response.headers["X-Process-Time"] = f"{elapsed:.4f}"
    logger.info(
        "path=%s method=%s status=%d latency=%.3fs",
        request.url.path,
        request.method,
        response.status_code,
        elapsed,
    )
    return response


# ---------------------------------------------------------------------------
# Exception handlers
# ---------------------------------------------------------------------------


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):  # noqa: ARG001
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(
            code="HTTP_ERROR",
            message=exc.detail,
        ).model_dump(),
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):  # noqa: ARG001
    logger.error("Unhandled exception: %s", exc, exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=ErrorResponse(
            code="INTERNAL_SERVER_ERROR",
            message="Đã xảy ra lỗi nội bộ. Vui lòng thử lại sau.",
        ).model_dump(),
    )


# ---------------------------------------------------------------------------
# Root & health
# ---------------------------------------------------------------------------


@app.get("/", tags=["Meta"])
def read_root():
    """API root – basic info."""
    return {
        "service": "Vinmec AI Triage API",
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/health",
    }


@app.get("/health", tags=["Meta"])
def health_check():
    """
    Liveness / readiness probe.

    Pings the database and reports system resource usage.
    """
    uptime = time.time() - START_TIME
    ram = psutil.virtual_memory().percent
    cpu = psutil.cpu_percent(interval=None)

    db_status = "disconnected"
    try:
        db_url = settings.DATABASE_URL
        engine = create_engine(db_url)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_status = "connected"
    except Exception as exc:  # noqa: BLE001
        db_status = f"error: {exc}"

    return {
        "status": "healthy",
        "database": db_status,
        "uptime_seconds": round(uptime, 2),
        "system": {
            "ram_percent": ram,
            "cpu_percent": cpu,
        },
        "config": {
            "chat_model": settings.OPENAI_CHAT_MODEL,
            "embedding_model": settings.OPENAI_EMBEDDING_MODEL,
            "red_flag_threshold": settings.RED_FLAG_SIMILARITY_THRESHOLD,
            "human_triage_threshold": settings.HUMAN_TRIAGE_CONFIDENCE_THRESHOLD,
            "sla_minutes": settings.QUEUE_SLA_MINUTES,
        },
    }


# ---------------------------------------------------------------------------
# Helper: build patient-facing message
# ---------------------------------------------------------------------------


def _build_patient_message(flow: str, triage: dict[str, Any]) -> str:
    """
    Compose a patient-facing message string from the pipeline result dict.
    """
    confidence: int = triage.get("confidence_score") or 0
    dept_name: str = triage.get("department_name") or "chuyên khoa phù hợp"
    follow_up: str | None = triage.get("follow_up_question")

    disclaimer = (
        "\n\n*⚠️ Đây là gợi ý tự động từ AI, không thay thế chẩn đoán y khoa. "
        "Vui lòng xác nhận với điều dưỡng hoặc bác sĩ.*"
    )

    if flow == "PENDING_HUMAN":
        base = (
            f"Hệ thống đang phân tích triệu chứng của bạn "
            f"(độ tin cậy: {confidence}%). "
            f"Điều dưỡng sẽ xem xét và xác nhận chuyên khoa phù hợp cho bạn sớm nhất."
        )
        if follow_up:
            base += f"\n\n🩺 {follow_up}"
        return base + disclaimer

    # AUTO_RESOLVED
    return (
        f"✅ {confidence}% phù hợp với **{dept_name}**. "
        f"Vui lòng đến quầy {dept_name} để được khám." + disclaimer
    )


# ---------------------------------------------------------------------------
# POST /api/v1/chat/triage
# ---------------------------------------------------------------------------


@app.post(
    "/api/v1/chat/triage",
    response_model=ChatResponse,
    status_code=status.HTTP_200_OK,
    tags=["Triage"],
    summary="Patient triage chat",
    responses={
        200: {
            "description": "Triage result (AUTO_RESOLVED / PENDING_HUMAN / EMERGENCY)"
        },
        429: {"description": "Rate limit exceeded"},
        500: {"description": "Internal server error"},
    },
)
@limiter.limit(settings.RATE_LIMIT_CHAT)
@observe()
async def chat_triage(
    request: Request,  # required by slowapi limiter
    body: ChatRequest,
):
    """
    Main patient-facing triage endpoint.

    Pipeline
    --------
    1. De-identify PII with Presidio.
    2. Extract core symptoms via LLM.
    3. Check semantic similarity against red-flag embeddings (pgvector).
       - If similarity > 0.85 → return EMERGENCY immediately.
    4. LLM routing: map symptoms to department + confidence score.
    5. Generate short clinical summary for nurse dashboard.
    6. Persist triage_log + human_triage_queue (if confidence < 85).

    Returns ``flow = AUTO_RESOLVED | PENDING_HUMAN | EMERGENCY``.
    """
    # Attach Langfuse trace metadata
    try:
        langfuse = get_client()
        langfuse.update_current_trace(
            session_id=body.session_id or str(uuid.uuid4()),
            user_id=body.patient_id,
            tags=["vinmec-triage", "v1"],
            metadata={
                "message_length": len(body.message),
                "has_history": bool(body.conversation_history),
            },
        )
    except Exception:  # noqa: BLE001
        pass  # Langfuse is optional – never block the request

    logger.info(
        "Triage request: patient_id=%s session=%s msg_len=%d",
        body.patient_id,
        body.session_id,
        len(body.message),
    )

    pipeline_result = await run_triage_pipeline(
        patient_id=body.patient_id,
        message=body.message,
        conversation_history=body.conversation_history or [],
    )

    flow_str: str = pipeline_result.get("flow", "PENDING_HUMAN")
    flow = TriageFlow(flow_str)

    # ------------------------------------------------------------------
    # EMERGENCY path
    # ------------------------------------------------------------------
    if flow == TriageFlow.EMERGENCY:
        emergency = EmergencyResult(
            matched_keyword=pipeline_result.get("matched_keyword") or "unknown",
            similarity_score=float(pipeline_result.get("similarity_score") or 0.0),
        )
        logger.warning(
            "EMERGENCY: patient=%s keyword='%s' score=%.4f",
            body.patient_id,
            emergency.matched_keyword,
            emergency.similarity_score,
        )
        return ChatResponse(
            status="success",
            flow=flow,
            emergency=emergency,
        )

    # ------------------------------------------------------------------
    # AUTO_RESOLVED / PENDING_HUMAN path
    # ------------------------------------------------------------------
    patient_msg = _build_patient_message(flow_str, pipeline_result)

    triage_result = TriageResult(
        department_code=pipeline_result.get("department_code"),
        department_name=pipeline_result.get("department_name"),
        confidence_score=pipeline_result.get("confidence_score"),
        message=patient_msg,
        follow_up_question=pipeline_result.get("follow_up_question"),
        queue_id=pipeline_result.get("queue_id"),
        clinical_summary=pipeline_result.get("clinical_summary"),
    )

    logger.info(
        "Triage done: patient=%s flow=%s dept=%s confidence=%s queue_id=%s",
        body.patient_id,
        flow_str,
        triage_result.department_code,
        triage_result.confidence_score,
        triage_result.queue_id,
    )

    return ChatResponse(
        status="success",
        flow=flow,
        result=triage_result,
    )


# ---------------------------------------------------------------------------
# GET /api/v1/queue/pending
# ---------------------------------------------------------------------------


@app.get(
    "/api/v1/queue/pending",
    response_model=PendingQueueResponse,
    status_code=status.HTTP_200_OK,
    tags=["Nurse Queue"],
    summary="List all pending triage queue items",
    responses={
        200: {"description": "List of pending queue items for the nurse dashboard"},
        503: {"description": "Database unavailable"},
    },
)
@limiter.limit("60/minute")
async def get_pending_queue_endpoint(request: Request):  # noqa: ARG001
    """
    Nurse dashboard polling endpoint.

    Returns all ``PENDING`` entries in ``human_triage_queue``, ordered
    oldest-first so the highest-SLA-risk cases appear at the top.
    Each item includes a computed ``minutes_waiting`` field and a
    ``sla_breached`` flag.
    """
    try:
        async with db_connection() as conn:
            rows = await get_pending_queue(conn)
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to fetch pending queue: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Không thể kết nối cơ sở dữ liệu. Vui lòng thử lại.",
        ) from exc

    now = datetime.now(timezone.utc)
    items: list[QueueItem] = []

    for row in rows:
        created_at: datetime = row["created_at"]
        # Ensure timezone-aware for arithmetic
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)

        minutes_waiting = (now - created_at).total_seconds() / 60.0
        sla_breached = minutes_waiting >= settings.QUEUE_SLA_MINUTES

        items.append(
            QueueItem(
                id=row["id"],
                patient_id=row["patient_id"],
                clinical_summary=row["clinical_summary"],
                suggested_dept=row.get("suggested_dept"),
                status=QueueStatus(row["status"]),
                created_at=created_at,
                minutes_waiting=round(minutes_waiting, 2),
                sla_breached=sla_breached,
            )
        )

    return PendingQueueResponse(total=len(items), items=items)


# ---------------------------------------------------------------------------
# POST /api/v1/queue/resolve
# ---------------------------------------------------------------------------


@app.post(
    "/api/v1/queue/resolve",
    response_model=ResolveResponse,
    status_code=status.HTTP_200_OK,
    tags=["Nurse Queue"],
    summary="Nurse resolves a pending triage queue item",
    responses={
        200: {"description": "Queue item resolved successfully"},
        404: {"description": "Queue item not found or already resolved"},
        503: {"description": "Database unavailable"},
    },
)
@limiter.limit("30/minute")
async def resolve_queue_endpoint(request: Request, body: ResolveRequest):  # noqa: ARG001
    """
    Nurse approves or corrects a triage decision.

    - Sets ``human_triage_queue.status = 'RESOLVED'``.
    - Back-fills ``triage_logs.final_dept`` and ``resolution_type`` to
      reinforce the semantic memory flywheel.

    Set ``resolution_type = NURSE_APPROVED`` when the AI suggestion was
    correct, or ``NURSE_CORRECTED`` when the nurse changed the department.
    """
    queue_id_str = str(body.queue_id)

    try:
        async with db_connection() as conn:
            updated = await resolve_queue_item(
                conn=conn,
                queue_id=queue_id_str,
                approved_dept=body.approved_dept,
                resolution_type=body.resolution_type.value,
            )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Failed to resolve queue item %s: %s", queue_id_str, exc, exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Không thể cập nhật cơ sở dữ liệu. Vui lòng thử lại.",
        ) from exc

    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Không tìm thấy mục chờ với ID {queue_id_str} "
                "hoặc mục này đã được xử lý."
            ),
        )

    logger.info(
        "Queue resolved: id=%s nurse=%s dept=%s type=%s",
        queue_id_str,
        body.nurse_id,
        body.approved_dept,
        body.resolution_type.value,
    )

    action = (
        "Đã duyệt"
        if body.resolution_type == ResolutionType.NURSE_APPROVED
        else "Đã sửa"
    )

    return ResolveResponse(
        success=True,
        queue_id=body.queue_id,
        final_dept=body.approved_dept,
        resolution_type=body.resolution_type,
        message=(
            f"{action}: bệnh nhân được điều phối đến khoa {body.approved_dept} "
            f"bởi điều dưỡng {body.nurse_id}."
        ),
    )


# ---------------------------------------------------------------------------
# POST /api/v1/queue/check-timeouts
# ---------------------------------------------------------------------------


@app.post(
    "/api/v1/queue/check-timeouts",
    response_model=TimeoutCheckResponse,
    status_code=status.HTTP_200_OK,
    tags=["Nurse Queue"],
    summary="Mark stale PENDING items as TIMEOUT (SLA sweep)",
    responses={
        200: {"description": "Timeout sweep completed"},
        503: {"description": "Database unavailable"},
    },
)
@limiter.limit("10/minute")
async def check_timeouts_endpoint(request: Request):  # noqa: ARG001
    """
    SLA enforcement sweep.

    Marks every ``PENDING`` queue item whose ``created_at`` is older than
    ``QUEUE_SLA_MINUTES`` (default: 3 min) as ``TIMEOUT``.

    This endpoint can be called:
    - On a schedule from an external cron / Supabase Edge Function.
    - Manually from the nurse dashboard.
    - As a background task triggered by other endpoints.
    """
    try:
        async with db_connection() as conn:
            count = await mark_timed_out_items(conn, settings.QUEUE_SLA_MINUTES)
    except Exception as exc:  # noqa: BLE001
        logger.error("Timeout sweep failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Không thể thực hiện kiểm tra SLA. Vui lòng thử lại.",
        ) from exc

    return TimeoutCheckResponse(
        success=True,
        timed_out_count=count,
        message=(
            f"Đã đánh dấu {count} mục TIMEOUT "
            f"(SLA = {settings.QUEUE_SLA_MINUTES} phút)."
        ),
    )


# ---------------------------------------------------------------------------
# POST /api/v1/admin/seed-red-flags
# ---------------------------------------------------------------------------


@app.post(
    "/api/v1/admin/seed-red-flags",
    response_model=SeedRedFlagsResponse,
    status_code=status.HTTP_200_OK,
    tags=["Admin"],
    summary="Seed red-flag emergency keyword embeddings into DB",
    responses={
        200: {"description": "Red-flag keywords seeded successfully"},
        503: {"description": "Database or OpenAI unavailable"},
    },
)
@limiter.limit(settings.RATE_LIMIT_ADMIN)
async def seed_red_flags_endpoint(request: Request):  # noqa: ARG001
    """
    Generate OpenAI embeddings for all 15 Vietnamese emergency red-flag
    keywords and upsert them into the ``red_flags`` table.

    This endpoint is **idempotent** – running it multiple times is safe;
    existing rows are updated with fresh embeddings via
    ``ON CONFLICT (keyword) DO UPDATE``.

    **When to call:**
    - Once after the initial DB migration.
    - After changing the embedding model to regenerate all vectors.

    **Security note:** Protect this endpoint with a reverse-proxy
    IP allow-list or an ``Authorization`` header in production.
    """
    keywords = settings.RED_FLAG_KEYWORDS

    try:
        async with db_connection() as conn:
            inserted = await seed_red_flags(conn, keywords)
    except Exception as exc:  # noqa: BLE001
        logger.error("Red-flag seeding failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Không thể seed red flags: {exc}",
        ) from exc

    logger.info("Red-flag seeding complete: %d/%d keywords", inserted, len(keywords))

    return SeedRedFlagsResponse(
        success=True,
        inserted=inserted,
        keywords=keywords,
        message=(
            f"Đã seed thành công {inserted}/{len(keywords)} từ khóa nguy hiểm "
            f"vào bảng red_flags."
        ),
    )
