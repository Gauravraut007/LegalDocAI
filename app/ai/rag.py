from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_text_splitters import RecursiveCharacterTextSplitter
from app.ai.document_processor import extract_text, split_text_into_chunks
from app.ai.llm import simple_chat
import os

def build_vector_store(file_path: str):
    text = extract_text(file_path)
    chunks = split_text_into_chunks(text)
    embeddings = GoogleGenerativeAIEmbeddings(
        model="models/embedding-001",
        google_api_key=os.getenv("GEMINI_API_KEY")
    )
    vector_store = FAISS.from_texts(chunks, embeddings)
    return vector_store

def query_document(file_path: str, question: str, top_k: int = 3) -> str:
    vector_store = build_vector_store(file_path)
    docs = vector_store.similarity_search(question, k=top_k)
    context = "\n\n".join([doc.page_content for doc in docs])
    prompt = f"""Use the following context to answer the question.

Context:
{context}

Question: {question}

Answer:"""
    return simple_chat(prompt)