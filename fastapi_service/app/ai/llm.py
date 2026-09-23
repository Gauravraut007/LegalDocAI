"""Gemini 1.5 Flash client with streaming, retries, and a circuit breaker."""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import AsyncIterator, List, Optional

from app.config import settings
from app.utils.retry import gemini_retry

logger = logging.getLogger(__name__)


# =========================================================================
# Circuit breaker
# =========================================================================
class _CircuitBreaker:
    def __init__(
        self,
        *,
        fail_threshold: int,
        window_seconds: int,
        open_seconds: int,
    ) -> None:
        self._fail_threshold = fail_threshold
        self._window = window_seconds
        self._open_seconds = open_seconds
        self._lock = threading.Lock()
        self._failures: list[float] = []
        self._opened_at: Optional[float] = None

    @property
    def state(self) -> str:
        with self._lock:
            if self._opened_at is None:
                return "closed"
            if time.time() - self._opened_at >= self._open_seconds:
                return "half_open"
            return "open"

    def allow(self) -> None:
        if self.state == "open":
            raise CircuitOpenError("Gemini circuit breaker is open")

    def record_success(self) -> None:
        with self._lock:
            self._failures.clear()
            self._opened_at = None

    def record_failure(self) -> None:
        now = time.time()
        with self._lock:
            self._failures = [t for t in self._failures if now - t <= self._window]
            self._failures.append(now)
            if len(self._failures) >= self._fail_threshold:
                self._opened_at = now
                logger.warning(
                    "circuit_breaker_open failures=%d window=%ds",
                    len(self._failures),
                    self._window,
                )


class CircuitOpenError(RuntimeError):
    pass


_BREAKER = _CircuitBreaker(
    fail_threshold=settings.LLM_CIRCUIT_FAIL_THRESHOLD,
    window_seconds=settings.LLM_CIRCUIT_WINDOW_SECONDS,
    open_seconds=settings.LLM_CIRCUIT_OPEN_SECONDS,
)


def get_breaker() -> _CircuitBreaker:
    return _BREAKER


# =========================================================================
# Configuration
# =========================================================================
_GEMINI_CONFIGURED = False
_CONFIGURE_LOCK = threading.Lock()


def _configure() -> None:
    global _GEMINI_CONFIGURED
    if _GEMINI_CONFIGURED:
        return
    with _CONFIGURE_LOCK:
        if _GEMINI_CONFIGURED:
            return
        if not settings.GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY is not configured")
        import google.generativeai as genai

        genai.configure(api_key=settings.GEMINI_API_KEY)
        _GEMINI_CONFIGURED = True


def _safety_settings():
    try:
        from google.generativeai.types import HarmBlockThreshold, HarmCategory

        return {
            HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
            HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
        }
    except Exception:  # noqa: BLE001
        return None


# =========================================================================
# Public API
# =========================================================================
@dataclass
class StreamChunk:
    text: str = ""
    finish_reason: Optional[str] = None
    usage: Optional[dict] = None


@dataclass
class CompletionResult:
    text: str
    usage: dict = field(default_factory=dict)


class GeminiClient:
    def __init__(
        self,
        *,
        model_name: Optional[str] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        max_output_tokens: Optional[int] = None,
    ) -> None:
        self.model_name = model_name or settings.GEMINI_MODEL
        self.temperature = (
            temperature if temperature is not None else settings.LLM_TEMPERATURE
        )
        self.top_p = top_p if top_p is not None else settings.LLM_TOP_P
        self.max_output_tokens = (
            max_output_tokens
            if max_output_tokens is not None
            else settings.LLM_MAX_OUTPUT_TOKENS
        )

    # ----------------------------------------------- one-shot generation
    @gemini_retry(max_attempts=3, min_wait=2.0, max_wait=20.0)
    def _generate_sync(
        self,
        *,
        system_prompt: str,
        contents: List[dict],
        temperature: Optional[float] = None,
        max_output_tokens: Optional[int] = None,
    ) -> CompletionResult:
        _configure()
        _BREAKER.allow()
        import google.generativeai as genai

        try:
            model = genai.GenerativeModel(
                self.model_name,
                system_instruction=system_prompt,
                safety_settings=_safety_settings(),
            )
            cfg = genai.types.GenerationConfig(
                temperature=temperature if temperature is not None else self.temperature,
                top_p=self.top_p,
                max_output_tokens=max_output_tokens or self.max_output_tokens,
            )
            resp = model.generate_content(contents, generation_config=cfg, stream=False)
            text = (resp.text or "") if hasattr(resp, "text") else ""
            usage = _extract_usage(resp)
            _BREAKER.record_success()
            return CompletionResult(text=text, usage=usage)
        except Exception:
            _BREAKER.record_failure()
            raise

    async def generate(
        self,
        *,
        system_prompt: str,
        user_message: str,
        history: Optional[List[dict]] = None,
        temperature: Optional[float] = None,
        max_output_tokens: Optional[int] = None,
    ) -> CompletionResult:
        contents = _to_contents(history or [], user_message)
        return await asyncio.to_thread(
            self._generate_sync,
            system_prompt=system_prompt,
            contents=contents,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )

    # ----------------------------------------------- streaming
    async def stream(
        self,
        *,
        system_prompt: str,
        user_message: str,
        history: Optional[List[dict]] = None,
    ) -> AsyncIterator[StreamChunk]:
        logger.info("gemini_stream_started model=%s", self.model_name)
        _configure()
        _BREAKER.allow()
        import google.generativeai as genai

        contents = _to_contents(history or [], user_message)
        cfg = genai.types.GenerationConfig(
            temperature=self.temperature,
            top_p=self.top_p,
            max_output_tokens=self.max_output_tokens,
        )

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue(maxsize=64)
        SENTINEL = object()

        def _worker() -> None:
            try:
                model = genai.GenerativeModel(
                    self.model_name,
                    system_instruction=system_prompt,
                    safety_settings=_safety_settings(),
                )
                try:
                    stream = model.generate_content(
                        contents,
                        generation_config=cfg,
                        stream=True,
                        request_options={
                            "timeout": settings.LLM_REQUEST_TIMEOUT_SECONDS,
                        },
                    )
                    finish_reason: Optional[str] = None
                    for event in stream:
                        txt = ""
                        try:
                            txt = event.text or ""
                        except Exception:  # noqa: BLE001
                            pass
                        if txt:
                            loop.call_soon_threadsafe(
                                queue.put_nowait, StreamChunk(text=txt)
                            )
                        if hasattr(event, "candidates") and event.candidates:
                            cand = event.candidates[0]
                            fr = getattr(cand, "finish_reason", None)
                            if fr is not None:
                                finish_reason = str(fr)
                    final_usage = _extract_usage(stream)
                    loop.call_soon_threadsafe(
                        queue.put_nowait,
                        StreamChunk(
                            finish_reason=finish_reason or "stop", usage=final_usage
                        ),
                    )
                    loop.call_soon_threadsafe(queue.put_nowait, SENTINEL)
                    _BREAKER.record_success()
                    return
                except Exception as stream_exc:  # noqa: BLE001
                    logger.warning(
                        "gemini_stream_timeout_fallback model=%s timeout=%ss error=%s",
                        self.model_name,
                        settings.LLM_REQUEST_TIMEOUT_SECONDS,
                        type(stream_exc).__name__,
                    )
                    resp = model.generate_content(
                        contents,
                        generation_config=cfg,
                        stream=False,
                    )
                    txt = (resp.text or "") if hasattr(resp, "text") else ""
                    if txt:
                        loop.call_soon_threadsafe(
                            queue.put_nowait, StreamChunk(text=txt)
                        )
                    final_usage = _extract_usage(resp)
                    loop.call_soon_threadsafe(
                        queue.put_nowait,
                        StreamChunk(finish_reason="stop", usage=final_usage),
                    )
                    loop.call_soon_threadsafe(queue.put_nowait, SENTINEL)
                    _BREAKER.record_success()
                    return
            except Exception as exc:  # noqa: BLE001
                _BREAKER.record_failure()
                loop.call_soon_threadsafe(queue.put_nowait, exc)
                loop.call_soon_threadsafe(queue.put_nowait, SENTINEL)

        worker_task = asyncio.create_task(asyncio.to_thread(_worker))
        try:
            while True:
                item = await queue.get()
                if item is SENTINEL:
                    logger.info("gemini_stream_finished model=%s", self.model_name)
                    break
                if isinstance(item, Exception):
                    logger.exception("gemini_stream_failed model=%s", self.model_name)
                    raise item
                yield item  # type: ignore[misc]
        finally:
            if not worker_task.done():
                worker_task.cancel()

    # ----------------------------------------------- token counting
    def count_tokens(self, text: str) -> int:
        try:
            _configure()
            import google.generativeai as genai

            model = genai.GenerativeModel(self.model_name)
            return int(model.count_tokens(text).total_tokens)
        except Exception:  # noqa: BLE001
            return max(1, len(text) // 4)


# =========================================================================
# Helpers
# =========================================================================
def _to_contents(history: List[dict], user_message: str) -> List[dict]:
    contents: List[dict] = []
    for turn in history:
        role = turn.get("role", "user")
        if role == "assistant":
            role = "model"
        elif role not in {"user", "model"}:
            continue
        contents.append(
            {"role": role, "parts": [{"text": str(turn.get("content", ""))}]}
        )
    contents.append({"role": "user", "parts": [{"text": user_message}]})
    return contents


def _extract_usage(resp_or_stream) -> dict:
    out: dict = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    meta = getattr(resp_or_stream, "usage_metadata", None)
    if meta is None:
        return out
    out["input_tokens"] = int(getattr(meta, "prompt_token_count", 0) or 0)
    out["output_tokens"] = int(getattr(meta, "candidates_token_count", 0) or 0)
    out["total_tokens"] = int(
        getattr(meta, "total_token_count", out["input_tokens"] + out["output_tokens"])
        or 0
    )
    return out
