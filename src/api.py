import time
import logging
from fastapi import FastAPI, Request
from .schema import ChatRequest, ChatResponse


app = FastAPI(title="VinUni Hackathon API")
logger = logging.getLogger("LLMOps")


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
def health():
    return {"status": "online", "message": "Ready for Hackathon"}


@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest):
    return ChatResponse(
        answer=f"Đã nhận tin nhắn: '{request.message}'. Agent đang được tích hợp.",
        sources=["mock_document.pdf"],
    )
