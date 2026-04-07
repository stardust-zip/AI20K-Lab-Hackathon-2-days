from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = "default"


class ChatResponse(BaseModel):
    answer: str
    sources: list[str] = []
    status: str = "success"
