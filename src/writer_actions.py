# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Sanad Arousi
"""Writer "rewrite the selection" / "generate at caret" logic — pure & testable.

Like :mod:`calc_actions`, this has **no** UNO and **no** networking imports and
is unit-testable on any Python 3.8+. Writer output is plain text (not a grid), so
this is simpler than the Calc side: build a prompt, send, and clean the reply.

The UNO glue that reads the selection / caret and writes the text lives in
:mod:`uno_bridge` (Writer section) and is tested against a real LibreOffice.
"""

from __future__ import annotations

import re
from typing import Any, Optional

__all__ = [
    "WriterActionError",
    "clean_output",
    "build_rewrite_system_prompt",
    "build_rewrite_user_prompt",
    "build_generate_system_prompt",
    "default_max_tokens",
    "rewrite_text",
    "generate_text",
    "summarize_text",
    "translate_text",
    "fix_grammar_text",
]


class WriterActionError(Exception):
    """The Writer action could not be completed (e.g. empty input)."""


# --------------------------------------------------------------------------- #
# Output cleaning
# --------------------------------------------------------------------------- #

_FENCE_BLOCK_RE = re.compile(
    r"^\s*```[^\n]*\n(?P<body>.*)\n```\s*$", re.DOTALL
)


def clean_output(text: str) -> str:
    """Trim whitespace and unwrap a single enclosing markdown code fence.

    We deliberately do NOT strip surrounding quotes — a legitimately quoted
    rewrite would be damaged. Only a *whole-output* ``` fence is removed.
    """
    if text is None:
        return ""
    match = _FENCE_BLOCK_RE.match(text.strip())
    if match:
        return match.group("body").strip()
    return text.strip()


_TRUNCATION_NOTE = ("\n\n[Claude's reply was cut off — raise max_tokens in "
                    "Claude ▸ Settings.]")


def _finish(result: Any) -> str:
    """Clean the reply and, if it was truncated, append a visible note so the
    cut-off is never inserted silently."""
    text = clean_output(result.text)
    if getattr(result, "truncated", False):
        text += _TRUNCATION_NOTE
    return text


# --------------------------------------------------------------------------- #
# Prompts
# --------------------------------------------------------------------------- #

def build_rewrite_system_prompt() -> str:
    return (
        "You are an in-document writing assistant for LibreOffice Writer. "
        "The user has selected some text and given an instruction describing how "
        "to change it.\n\n"
        "Return ONLY the revised text that should replace the selection. Do not "
        "add any preamble, explanation, quotation marks, or markdown code fences. "
        "Preserve the language of the original unless the instruction says otherwise."
    )


def build_rewrite_user_prompt(selected_text: str, instruction: str) -> str:
    return (
        "Instruction:\n{instruction}\n\n"
        "Selected text:\n{text}"
    ).format(instruction=instruction.strip(), text=selected_text)


def build_generate_system_prompt() -> str:
    return (
        "You are an in-document writing assistant for LibreOffice Writer. "
        "The user's cursor is at a point in their document and they have given an "
        "instruction describing text to produce.\n\n"
        "Return ONLY the text to insert at the cursor. Do not add any preamble, "
        "explanation, quotation marks, or markdown code fences."
    )


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #

def default_max_tokens(text: str) -> int:
    """Scale the output budget to the input length (bounded to [512, 8192])."""
    # ~1 token per 3-4 chars; a headroom of 512 lets the rewrite grow.
    approx = int(len(text or "") / 2)
    return max(512, min(8192, approx + 512))


def rewrite_text(
    client: Any,
    selected_text: str,
    instruction: str,
    *,
    model: Optional[str] = None,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
) -> str:
    """Rewrite ``selected_text`` per ``instruction`` and return the cleaned reply."""
    if not selected_text or not selected_text.strip():
        raise WriterActionError("There is no selected text to rewrite.")
    result = client.send(
        system=build_rewrite_system_prompt(),
        prompt=build_rewrite_user_prompt(selected_text, instruction),
        model=model,
        max_tokens=max_tokens or default_max_tokens(selected_text),
        temperature=temperature,
    )
    return _finish(result)


def generate_text(
    client: Any,
    instruction: str,
    *,
    model: Optional[str] = None,
    max_tokens: int = 1024,
    temperature: Optional[float] = None,
) -> str:
    """Generate text from ``instruction`` (for insert-at-caret) and clean it."""
    if not instruction or not instruction.strip():
        raise WriterActionError("Please enter an instruction.")
    result = client.send(
        system=build_generate_system_prompt(),
        prompt=instruction.strip(),
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    return _finish(result)


# --------------------------------------------------------------------------- #
# Named commands — thin wrappers over rewrite_text with a canned instruction
# (the ".oxt" Claude menu: Summarize / Translate / Fix Grammar). Kept here so
# the prompt wording is unit-testable with a fake client, no UNO/network.
# --------------------------------------------------------------------------- #

def summarize_text(client: Any, selected_text: str, **kw: Any) -> str:
    """Summarize the selection (returned for insert, not replace)."""
    return rewrite_text(
        client, selected_text,
        "Summarize the following text concisely in the same language, "
        "preserving its key points.", **kw)


def translate_text(client: Any, selected_text: str, language: str, **kw: Any) -> str:
    """Translate the selection into ``language`` (returned for replace)."""
    if not language or not language.strip():
        raise WriterActionError("Please specify a target language.")
    return rewrite_text(
        client, selected_text,
        "Translate the following text into %s. Output only the translation, "
        "preserving formatting and line breaks." % language.strip(), **kw)


def fix_grammar_text(client: Any, selected_text: str, **kw: Any) -> str:
    """Correct spelling/grammar/punctuation without changing meaning/language."""
    return rewrite_text(
        client, selected_text,
        "Correct spelling, grammar, and punctuation. Do not change the meaning, "
        "tone, or language.", **kw)
