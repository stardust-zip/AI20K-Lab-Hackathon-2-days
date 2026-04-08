"""
schema.py – Pydantic models for the Vinmec AI Triage API.

Every request body, response body, and internal data-transfer object used
across the FastAPI application is defined here so that validation, serialisation
and OpenAPI docs are all driven from a single source of truth.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Enumerations (mirror the PostgreSQL ENUM types in init.sql)
# ---------------------------------------------------------------------------


class TriageFlow(str, Enum):
    """High-level outcome of the triage pipeline returned to the frontend."""

    AUTO_RESOLVED = "AUTO_RESOLVED"
    """AI confidence ≥ 85 – routed automatically, no human needed."""

    PENDING_HUMAN = "PENDING_HUMAN"
    """AI confidence < 85 – queued for nurse review."""

    EMERGENCY = "EMERGENCY"
    """Red-flag similarity > 0.85 – bypass LLM, trigger 115 emergency flow."""


class ResolutionType(str, Enum):
    """How a triage case was ultimately resolved (stored in triage_logs)."""

    AI_AUTO = "AI_AUTO"
    NURSE_APPROVED = "NURSE_APPROVED"
    NURSE_CORRECTED = "NURSE_CORRECTED"
    DOCTOR_CORRECTED = "DOCTOR_CORRECTED"


class QueueStatus(str, Enum):
    """Lifecycle status of a human-triage queue entry."""

    PENDING = "PENDING"
    RESOLVED = "RESOLVED"
    TIMEOUT = "TIMEOUT"


# ---------------------------------------------------------------------------
# Chat / Triage endpoint models
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    """
    Payload sent by the patient-facing frontend to ``POST /api/v1/chat/triage``.
    """

    patient_id: str = Field(
        ...,
        description="Opaque patient identifier (does NOT need to be a real name).",
        examples=["PAT-00123"],
    )
    message: str = Field(
        ...,
        min_length=1,
        max_length=4_000,
        description="Raw free-text symptom description from the patient.",
        examples=["Tôi bị đau bụng dưới bên phải, buồn nôn suốt từ sáng."],
    )
    session_id: str | None = Field(
        default=None,
        description="Optional conversation session UUID for multi-turn chat tracking.",
        examples=["550e8400-e29b-41d4-a716-446655440000"],
    )
    conversation_history: list[dict[str, str]] = Field(
        default_factory=list,
        description=(
            "Previous turns in the current conversation.  "
            "Each element must have 'role' ('user' | 'assistant') and 'content' keys."
        ),
        examples=[
            [
                {"role": "user", "content": "Tôi đau đầu"},
                {"role": "assistant", "content": "Bạn đau đầu bao lâu rồi?"},
            ]
        ],
    )

    @field_validator("conversation_history")
    @classmethod
    def validate_history(cls, v: list[dict[str, str]]) -> list[dict[str, str]]:
        for turn in v:
            if "role" not in turn or "content" not in turn:
                raise ValueError(
                    "Each conversation_history item must have 'role' and 'content' keys."
                )
            if turn["role"] not in {"user", "assistant", "system"}:
                raise ValueError(
                    f"Invalid role '{turn['role']}'. Must be 'user', 'assistant', or 'system'."
                )
        return v


class TriageResult(BaseModel):
    """
    Core triage outcome embedded inside ``ChatResponse.result``.
    Present for AUTO_RESOLVED and PENDING_HUMAN flows.
    """

    department_code: str | None = Field(
        default=None,
        description="Internal department code (e.g. 'NGOAI_TH').",
        examples=["NGOAI_TH"],
    )
    department_name: str | None = Field(
        default=None,
        description="Human-readable department name in Vietnamese.",
        examples=["Ngoại Tiêu hoá"],
    )
    confidence_score: int | None = Field(
        default=None,
        ge=0,
        le=100,
        description="AI confidence score 0-100.",
        examples=[92],
    )
    message: str = Field(
        ...,
        description="Patient-facing message explaining the triage decision.",
        examples=[
            "92% phù hợp Khám Ngoại Tiêu hoá. *Đây là gợi ý tự động, vui lòng xác nhận với điều dưỡng.*"
        ],
    )
    follow_up_question: str | None = Field(
        default=None,
        description="Follow-up question from AI when confidence < 85.",
        examples=["Bạn có bị sốt hoặc tiêu chảy kèm theo không?"],
    )
    queue_id: UUID | None = Field(
        default=None,
        description="UUID of the human_triage_queue record (only set when flow=PENDING_HUMAN).",
    )
    clinical_summary: str | None = Field(
        default=None,
        description="Short clinical summary generated for the nurse dashboard.",
    )


class EmergencyResult(BaseModel):
    """Result payload returned when a red-flag emergency is detected."""

    matched_keyword: str = Field(
        ...,
        description="The red-flag keyword whose embedding was most similar to the input.",
        examples=["đột quỵ"],
    )
    similarity_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Cosine similarity score that triggered the emergency flow.",
        examples=[0.93],
    )
    message: str = Field(
        default=(
            "🚨 Phát hiện triệu chứng nguy hiểm. "
            "Vui lòng gọi ngay 115 hoặc đến phòng Cấp Cứu gần nhất!"
        ),
        description="Patient-facing emergency message.",
    )
    instructions: list[str] = Field(
        default_factory=lambda: [
            "Gọi ngay số khẩn cấp 115.",
            "Đến phòng Cấp Cứu (Emergency) gần nhất.",
            "Không tự lái xe – nhờ người đưa hoặc gọi xe cấp cứu.",
            "Giữ bình tĩnh và theo dõi các dấu hiệu sinh tồn.",
        ],
        description="Step-by-step emergency instructions in Vietnamese.",
    )


class ChatResponse(BaseModel):
    """
    Response returned by ``POST /api/v1/chat/triage``.

    The ``flow`` field determines which sub-field of ``result`` / ``emergency``
    is populated:
    - AUTO_RESOLVED  → ``result`` is a TriageResult (no queue_id)
    - PENDING_HUMAN  → ``result`` is a TriageResult with queue_id set
    - EMERGENCY      → ``emergency`` is an EmergencyResult
    """

    status: str = Field(
        default="success",
        description="HTTP-level status string.",
        examples=["success", "error"],
    )
    flow: TriageFlow = Field(
        ...,
        description="High-level pipeline outcome.",
    )
    result: TriageResult | None = Field(
        default=None,
        description="Triage result (present for AUTO_RESOLVED and PENDING_HUMAN flows).",
    )
    emergency: EmergencyResult | None = Field(
        default=None,
        description="Emergency details (present only for EMERGENCY flow).",
    )
    # Legacy field kept for the existing /chat stub – will not be populated by triage
    answer: str | None = Field(default=None, exclude=True)
    sources: list[str] = Field(default_factory=list, exclude=True)

    model_config = {"populate_by_name": True}


# ---------------------------------------------------------------------------
# Nurse queue models
# ---------------------------------------------------------------------------


class QueueItem(BaseModel):
    """
    A single entry in the human-triage queue, returned by
    ``GET /api/v1/queue/pending``.
    """

    id: UUID = Field(..., description="Primary key of the human_triage_queue row.")
    patient_id: str = Field(..., description="Opaque patient identifier.")
    clinical_summary: str = Field(
        ...,
        description="AI-generated clinical summary for quick nurse assessment.",
    )
    suggested_dept: str | None = Field(
        default=None,
        description="Department code the AI suggested (may be None for low-confidence cases).",
    )
    status: QueueStatus = Field(..., description="Current lifecycle status.")
    created_at: datetime = Field(
        ...,
        description="Timestamp when the queue entry was created (SLA timer start).",
    )
    minutes_waiting: float | None = Field(
        default=None,
        description="Minutes elapsed since created_at (computed, not stored in DB).",
    )
    sla_breached: bool | None = Field(
        default=None,
        description="True when the item has been waiting longer than the configured SLA.",
    )

    model_config = {"from_attributes": True}


class PendingQueueResponse(BaseModel):
    """Response wrapper for ``GET /api/v1/queue/pending``."""

    total: int = Field(..., description="Total number of pending items returned.")
    items: list[QueueItem] = Field(..., description="List of pending queue entries.")


class ResolveRequest(BaseModel):
    """
    Payload sent by the nurse when approving or correcting a triage decision
    via ``POST /api/v1/queue/resolve``.
    """

    queue_id: UUID = Field(
        ...,
        description="UUID of the human_triage_queue entry to resolve.",
    )
    approved_dept: str = Field(
        ...,
        description="Department code the nurse approved or corrected to.",
        examples=["TIM_MACH"],
    )
    nurse_id: str = Field(
        ...,
        description="Identifier of the nurse performing the resolution.",
        examples=["NURSE-007"],
    )
    resolution_type: ResolutionType = Field(
        default=ResolutionType.NURSE_APPROVED,
        description=(
            "Whether the nurse agreed with the AI suggestion (NURSE_APPROVED) "
            "or overrode it (NURSE_CORRECTED)."
        ),
    )
    notes: str | None = Field(
        default=None,
        max_length=1_000,
        description="Optional free-text notes from the nurse.",
    )


class ResolveResponse(BaseModel):
    """Response returned after a nurse resolves a queue entry."""

    success: bool = Field(..., description="Whether the resolution succeeded.")
    queue_id: UUID = Field(..., description="The resolved queue entry UUID.")
    final_dept: str = Field(
        ..., description="Department code that was ultimately assigned."
    )
    resolution_type: ResolutionType = Field(
        ..., description="How the case was resolved."
    )
    message: str = Field(..., description="Human-readable confirmation message.")


# ---------------------------------------------------------------------------
# Admin / seeding models
# ---------------------------------------------------------------------------


class SeedRedFlagsResponse(BaseModel):
    """Response returned by ``POST /api/v1/admin/seed-red-flags``."""

    success: bool
    inserted: int = Field(
        ..., description="Number of red-flag keywords inserted/updated."
    )
    keywords: list[str] = Field(..., description="The keywords that were seeded.")
    message: str


# ---------------------------------------------------------------------------
# Timeout check models
# ---------------------------------------------------------------------------


class TimeoutCheckResponse(BaseModel):
    """Response returned by ``POST /api/v1/queue/check-timeouts``."""

    success: bool
    timed_out_count: int = Field(
        ...,
        description="Number of queue items that were marked TIMEOUT in this run.",
    )
    message: str


# ---------------------------------------------------------------------------
# Generic error model
# ---------------------------------------------------------------------------


class ErrorResponse(BaseModel):
    """Standard error envelope used for 4xx / 5xx responses."""

    status: str = "error"
    code: str = Field(
        ..., description="Machine-readable error code.", examples=["VALIDATION_ERROR"]
    )
    message: str = Field(..., description="Human-readable error description.")
    details: Any | None = Field(
        default=None, description="Optional structured error details."
    )
