from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence


_ALLOWED_SUBPROCESS_ENVIRONMENT = {
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "WINDIR",
    "TEMP",
    "TMP",
    "HOME",
    "USERPROFILE",
    "LANG",
    "LC_ALL",
}

_BEARER_PATTERN = re.compile(r"(?i)\bBearer\s+[^\s,;]+")
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)(\b[A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD)\b\s*[:=]\s*)"
    r"([^\s,;]+)"
)


def redact(value: object, secrets: Sequence[str] = ()) -> str:
    text = str(value)
    for secret in sorted({item for item in secrets if item}, key=len, reverse=True):
        text = text.replace(secret, "<REDACTED>")
    text = _BEARER_PATTERN.sub("Bearer <REDACTED>", text)
    return _SECRET_ASSIGNMENT_PATTERN.sub(r"\1<REDACTED>", text)


class Redactor:
    def __init__(self, secrets: Sequence[str]) -> None:
        self._secrets = tuple(
            sorted({secret for secret in secrets if secret}, key=len, reverse=True)
        )

    def redact(self, value: object) -> str:
        return redact(value, self._secrets)

    def redact_object(self, value: object) -> object:
        if isinstance(value, Mapping):
            return {
                self.redact(key) if isinstance(key, str) else key: self.redact_object(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self.redact_object(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self.redact_object(item) for item in value)
        if isinstance(value, str) or isinstance(value, BaseException):
            return self.redact(value)
        return value


def sanitized_subprocess_environment(
    source: Mapping[str, str] | None = None,
    api_key: str | None = None,
) -> dict[str, str]:
    environment = os.environ if source is None else source
    cleaned: dict[str, str] = {}
    for name, value in environment.items():
        normalized = name.upper()
        if normalized not in _ALLOWED_SUBPROCESS_ENVIRONMENT:
            continue
        if (
            normalized == "AUTHORIZATION"
            or normalized.endswith("_API_KEY")
            or normalized.endswith("_TOKEN")
            or normalized.endswith("_SECRET")
        ):
            continue
        if api_key and api_key in value:
            continue
        cleaned[name] = value
    return cleaned
