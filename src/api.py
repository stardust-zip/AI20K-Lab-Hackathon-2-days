import time
import logging
import psutil
from fastapi import FastAPI, Request
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from .schema import ChatRequest, ChatResponse

limiter = Limiter(key_func=get_remote_address)

app = FastAPI(title="VinUni Hackathon API")
app.state.limiter = limiter

app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore

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


@app.get("/health")
def advanced_health_check():
    uptime_seconds = time.time() - START_TIME
    ram_usage = psutil.virtual_memory().percent
    cpu_usage = psutil.cpu_percent()

    return {
        "status": "healthy",
        "uptime": f"{uptime_seconds:.2f} seconds",
        "system_metrics": {
            "ram_usage_percent": ram_usage,
            "cpu_usage_percent": cpu_usage,
        },
        "dependencies": {
            "database": "disconnected_for_now",  # Sáng mai update khi có Supabase
            "llm_api": "ready",
        },
    }


@app.post("/chat", response_model=ChatResponse)
@limiter.limit("5/minute")
async def chat_endpoint(request: Request, body: ChatRequest):
    return ChatResponse(
        answer=f"Đã nhận tin nhắn: '{body.message}'. Agent đang được tích hợp.",
        sources=["mock_document.pdf"],
    )
