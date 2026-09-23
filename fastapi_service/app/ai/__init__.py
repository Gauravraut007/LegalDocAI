from app.ai.chunker import Chunk, DocumentChunker
from app.ai.classifier import classify_document
from app.ai.embedder import LocalEmbedder, get_model
from app.ai.evaluation import (
    evaluate_answer_quality,
    ground_answer_in_sources,
    retrieval_coverage_score,
)
from app.ai.llm import GeminiClient, StreamChunk, get_breaker
from app.ai.ocr_engine import OCREngine, OCRResult, PageResult
from app.ai.prompts import REFUSAL_PHRASE, build_user_message, select_prompt
from app.ai.rag import RAGEvent, RAGRequest, RAGService, Source
from app.ai.retriever import RetrievedChunk, Retriever
from app.ai.vectorstore import (
    FaissVectorStore,
    IndexMeta,
    SearchHit,
    collection_name_for,
    index_path,
    meta_path,
)

__all__ = [
    "Chunk",
    "DocumentChunker",
    "classify_document",
    "LocalEmbedder",
    "get_model",
    "evaluate_answer_quality",
    "ground_answer_in_sources",
    "retrieval_coverage_score",
    "OCREngine",
    "OCRResult",
    "PageResult",
    "FaissVectorStore",
    "IndexMeta",
    "SearchHit",
    "collection_name_for",
    "index_path",
    "meta_path",
    "GeminiClient",
    "StreamChunk",
    "get_breaker",
    "REFUSAL_PHRASE",
    "build_user_message",
    "select_prompt",
    "RAGService",
    "RAGRequest",
    "RAGEvent",
    "Source",
    "Retriever",
    "RetrievedChunk",
]
