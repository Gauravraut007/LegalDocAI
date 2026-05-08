from fastapi import APIRouter, UploadFile, File, HTTPException
from pydantic import BaseModel
from app.ai.llm import simple_chat, chat_with_history
from app.ai.rag import query_document
from app.ai.document_processor import extract_text
import shutil
import os

router = APIRouter(prefix="/ai", tags=["AI"])

UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

class ChatRequest(BaseModel):
    prompt: str

class ChatHistoryRequest(BaseModel):
    messages: list

class RAGRequest(BaseModel):
    question: str
    file_path: str

@router.post("/chat")
def chat(request: ChatRequest):
    try:
        response = simple_chat(request.prompt)
        return {"response": response}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/chat/history")
def chat_history(request: ChatHistoryRequest):
    try:
        response = chat_with_history(request.messages)
        return {"response": response}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/upload")
def upload_file(file: UploadFile = File(...)):
    try:
        file_path = os.path.join(UPLOAD_DIR, file.filename)
        with open(file_path, "wb") as f:
            shutil.copyfileobj(file.file, f)
        text = extract_text(file_path)
        return {"filename": file.filename, "file_path": file_path, "preview": text[:500]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/rag")
def rag_query(request: RAGRequest):
    try:
        response = query_document(request.file_path, request.question)
        return {"response": response}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))