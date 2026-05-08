"""Optional smoke test against the real Gemini API.

Skipped unless ``GEMINI_API_KEY`` is set. Issues a single non-streaming call.
"""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("GEMINI_API_KEY"), reason="GEMINI_API_KEY not set"
)


@pytest.mark.asyncio
async def test_real_gemini_smoke() -> None:
    from app.ai.llm import GeminiClient

    client = GeminiClient(max_output_tokens=50)
    res = await client.generate(
        system_prompt="You answer with one short sentence.",
        user_message="Reply with exactly: pong.",
    )
    assert res.text.strip()
