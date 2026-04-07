from fastapi import FastAPI
from .schema import ChatRequest, ChatResponse
# from .agent import run_agent_loop 

app = FastAPI(title="VinUni Hackathon API")

@app.get("/health")
def health():
    return {"status": "online", "message": "Ready for Hackathon"}

@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest):
    # GIAI ĐOẠN ĐẦU: Trả về Mock Data để FE làm việc
    return ChatResponse(
        answer=f"Đã nhận tin nhắn: '{request.message}'. Agent đang được tích hợp.",
        sources=["mock_document.pdf"]
    )
