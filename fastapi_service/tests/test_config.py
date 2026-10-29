from app.config import settings


def test_default_gemini_model_is_the_current_supported_value() -> None:
    assert settings.GEMINI_MODEL == "gemini-3.8-flash"
