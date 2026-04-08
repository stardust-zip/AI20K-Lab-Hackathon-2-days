import time
import os
import logging
import psutil
import openai
from fastapi import FastAPI, Request
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from .schema import ChatRequest, ChatResponse
from sqlalchemy import create_engine, text

from langfuse import observe, get_client

limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="VinUni Hackathon API")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

logger = logging.getLogger("LLMOps")
START_TIME = time.time()


@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    process_time = time.time() - start_time
    response.headers["X-Process-Time"] = str(process_time)

    logger.info(
        f"Endpoint: {request.url.path} | "
        f"Method: {request.method} | "
        f"Status: {response.status_code} | "
        f"Latency: {process_time:.3f}s"
    )
    return response


@app.get("/")
def read_root():
    return {
        "message": "AI Agent API is running! Check /docs for Swagger UI or /health for metrics."
    }


@app.get("/health")
def health_check():
    uptime_seconds = time.time() - START_TIME
    ram_usage = psutil.virtual_memory().percent
    cpu_usage = psutil.cpu_percent()

    db_status = "disconnected"
    try:
        # Fetch the URL from the environment (Railway injects this)
        db_url = os.getenv("DATABASE_URL")
        if db_url:
            # Quick engine just for the ping
            engine = create_engine(db_url)
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            db_status = "connected"
    except Exception as e:
        db_status = f"error: {str(e)}"

    return {
        "status": "healthy",
        "database": db_status,
        "uptime": f"{uptime_seconds:.2f} seconds",
        "system_metrics": {
            "ram_usage_percent": ram_usage,
            "cpu_usage_percent": cpu_usage,
        },
        "dependencies": {
            "llm_api": "ready",
        },
    }


@app.post("/chat", response_model=ChatResponse)
@limiter.limit("5/minute")
@observe()
async def chat_endpoint(request: Request, body: ChatRequest):
    langfuse = get_client()
    langfuse.update_current_trace(
        session_id=body.session_id,
        user_id="hackathon_demo_user",
        tags=["hackathon-demo", "v1-agent"],
    )

    answer = (
        f"Đã nhận tin nhắn: '{body.message}'. Agent đang được theo dõi qua Langfuse."
    )

    return ChatResponse(
        answer=answer,
        sources=["mock_document.pdf"],
    )
