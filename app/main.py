from fastapi import FastAPI
from app.database import Base, engine
from app.routes.ai import router as ai_router

Base.metadata.create_all(bind=engine)

app = FastAPI(title="FastAPI Starter", version="1.0.0")

app.include_router(ai_router)

@app.get("/")
def root():
    return {"message": "FastAPI is running!"}

@app.get("/health")
def health():
    return {"status": "ok"}