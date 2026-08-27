from __future__ import annotations

import os
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


class Redactor:
    def __init__(self, secrets: Sequence[str]) -> None:
        self._secrets = tuple(
            sorted({secret for secret in secrets if secret}, key=len, reverse=True)
        )

    def redact(self, value: object) -> str:
        text = str(value)
        for secret in self._secrets:
            text = text.replace(secret, "<REDACTED>")
        return text


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
