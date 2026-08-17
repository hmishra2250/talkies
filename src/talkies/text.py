from __future__ import annotations

import re

SPOKEN_COMMANDS = (
    (re.compile(r"\bnew paragraph\b[,.]?", re.IGNORECASE), "\n\n"),
    (re.compile(r"\bnew line\b[,.]?", re.IGNORECASE), "\n"),
)


def normalize_text(
    text: str,
    *,
    replacements: dict[str, str] | None = None,
    spoken_commands: bool = True,
) -> str:
    value = re.sub(r"[ \t]+", " ", text).strip()
    if spoken_commands:
        for pattern, replacement in SPOKEN_COMMANDS:
            value = pattern.sub(replacement, value)
    for source, target in (replacements or {}).items():
        value = re.sub(
            rf"\b{re.escape(source)}\b",
            lambda _, replacement=target: replacement,
            value,
            flags=re.IGNORECASE,
        )
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()
