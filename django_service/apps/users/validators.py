"""Password complexity validator."""
from __future__ import annotations

import re

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _


class ComplexityValidator:
    """Require at least one upper, one lower, one digit, and one symbol."""

    UPPER = re.compile(r"[A-Z]")
    LOWER = re.compile(r"[a-z]")
    DIGIT = re.compile(r"[0-9]")
    SYMBOL = re.compile(r"[^A-Za-z0-9]")

    def validate(self, password, user=None):  # noqa: ARG002
        missing = []
        if not self.UPPER.search(password):
            missing.append("uppercase letter")
        if not self.LOWER.search(password):
            missing.append("lowercase letter")
        if not self.DIGIT.search(password):
            missing.append("digit")
        if not self.SYMBOL.search(password):
            missing.append("symbol")
        if missing:
            raise ValidationError(
                _("Password must contain at least one %(items)s."),
                code="password_complexity",
                params={"items": ", ".join(missing)},
            )

    def get_help_text(self):
        return _(
            "Password must contain at least one uppercase letter, one lowercase "
            "letter, one digit and one symbol."
        )
