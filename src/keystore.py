# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Sanad Arousi
"""Secure-ish storage for the Anthropic API key.

Resolution order for :func:`get_api_key`:
  1. The ``ANTHROPIC_API_KEY`` environment variable (developer override).
  2. A stored key in the user's config dir:
       * **Windows** — encrypted at rest with **DPAPI** (per-user, via ``ctypes``;
         no third-party dependency). This mirrors best-in-class prior art.
       * **Other OSes** — base64 in a `0600` file, clearly marked NOT encrypted
         (documented limitation; prefer the env var or an OS keyring there).

The key is never written to the JSON config or committed anywhere.
"""

from __future__ import annotations

import base64
import os
import sys
from typing import Optional

try:
    from . import config as _config          # packaged in the .oxt (claudeconn package)
except ImportError:                          # flat layout (tests / dev)
    import config as _config

ENV_VAR = "ANTHROPIC_API_KEY"
KEY_FILE_WIN = "apikey.dpapi"     # DPAPI ciphertext (base64)
KEY_FILE_PLAIN = "apikey.plain"   # base64 only (non-Windows fallback)

IS_WINDOWS = sys.platform.startswith("win")


# --------------------------------------------------------------------------- #
# Windows DPAPI via ctypes
# --------------------------------------------------------------------------- #

def _dpapi_available() -> bool:
    return IS_WINDOWS


def _dpapi_crypt(data: bytes, encrypt: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_char))]

    def to_blob(raw: bytes) -> DATA_BLOB:
        buf = ctypes.create_string_buffer(raw, len(raw))
        return DATA_BLOB(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    fn = crypt32.CryptProtectData if encrypt else crypt32.CryptUnprotectData

    in_blob = to_blob(data)
    out_blob = DATA_BLOB()
    # flags = CRYPTPROTECT_UI_FORBIDDEN (0x1) so it never pops a UI.
    ok = fn(ctypes.byref(in_blob), None, None, None, None, 0x1, ctypes.byref(out_blob))
    if not ok:
        raise OSError("DPAPI %s failed" % ("encrypt" if encrypt else "decrypt"))
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #

def _win_path(base: Optional[str]) -> str:
    return os.path.join(_config.config_dir(base), KEY_FILE_WIN)


def _plain_path(base: Optional[str]) -> str:
    return os.path.join(_config.config_dir(base), KEY_FILE_PLAIN)


def _write_private(path: str, data: bytes) -> None:
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    # Create with 0o600 FROM THE START via os.open — no world-readable window
    # between create and chmod. (On Windows the mode is largely ignored but the
    # DPAPI ciphertext, not the raw key, is what is written there anyway.)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(path, flags, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

def set_api_key(key: str, base: Optional[str] = None) -> str:
    """Store ``key`` for later retrieval. Returns the file path written.

    Raises ``ValueError`` for an empty key.
    """
    key = (key or "").strip()
    if not key:
        raise ValueError("API key must not be empty.")
    # Remove any stale copy in the other format first.
    clear_api_key(base)
    if _dpapi_available():
        blob = _dpapi_crypt(key.encode("utf-8"), encrypt=True)
        path = _win_path(base)
        _write_private(path, base64.b64encode(blob))
    else:
        path = _plain_path(base)
        _write_private(path, base64.b64encode(key.encode("utf-8")))
    return path


def get_api_key(base: Optional[str] = None) -> Optional[str]:
    """Return the key from the env var, else from storage, else ``None``."""
    env = os.environ.get(ENV_VAR)
    if env and env.strip():
        return env.strip()

    win = _win_path(base)
    if os.path.exists(win):
        try:
            with open(win, "rb") as handle:
                blob = base64.b64decode(handle.read())
            return _dpapi_crypt(blob, encrypt=False).decode("utf-8")
        except (OSError, ValueError):
            return None

    plain = _plain_path(base)
    if os.path.exists(plain):
        try:
            with open(plain, "rb") as handle:
                return base64.b64decode(handle.read()).decode("utf-8")
        except (OSError, ValueError):
            return None

    return None


def has_stored_key(base: Optional[str] = None) -> bool:
    """True if a key is stored on disk (ignores the env var)."""
    return os.path.exists(_win_path(base)) or os.path.exists(_plain_path(base))


def clear_api_key(base: Optional[str] = None) -> None:
    """Delete any stored key file (both formats)."""
    for path in (_win_path(base), _plain_path(base)):
        try:
            os.remove(path)
        except OSError:
            pass
