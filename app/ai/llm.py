from google import genai
from dotenv import load_dotenv
import os

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=GEMINI_API_KEY)

def simple_chat(prompt: str, model_name: str = "gemini-2.0-flash") -> str:
    response = client.models.generate_content(
        model=model_name,
        contents=prompt
    )
    return response.text

def chat_with_history(messages: list, model_name: str = "gemini-2.0-flash") -> str:
    history = []
    for msg in messages[:-1]:
        history.append({
            "role": msg["role"],
            "parts": [{"text": msg["content"]}]
        })
    chat = client.chats.create(model=model_name, history=history)
    response = chat.send_message(messages[-1]["content"])
    return response.text