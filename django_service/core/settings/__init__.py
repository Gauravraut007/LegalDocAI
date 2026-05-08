"""Default settings entry — re-exports development settings."""
import os

env = os.environ.get("DJANGO_SETTINGS_MODULE", "")
if env.endswith("production"):
    from core.settings.production import *  # noqa: F401,F403
else:
    from core.settings.development import *  # noqa: F401,F403
