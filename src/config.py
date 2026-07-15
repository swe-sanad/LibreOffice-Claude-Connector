# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Persistent user settings (model, timeout, ...) stored as JSON per user.

The API key is deliberately NOT stored here — see :mod:`keystore`. Everything is
pure stdlib and unit-testable by passing a temporary ``base`` directory.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

APP_DIR_NAME = "LibreOffice-Claude-Connector"
CONFIG_FILE = "config.json"

# Only keys present here are persisted / accepted from disk.
DEFAULTS: Dict[str, Any] = {
    "provider": "anthropic",             # "anthropic" | "openai_compatible" (Ollama/LM Studio/…)
    "model": "claude-sonnet-5",          # dateless pinned snapshot; user-editable
    "temperature": None,                 # None -> omit (API default)
    "max_tokens": None,                  # None -> per-action default
    "timeout": 120.0,                    # seconds; never None (would hang the UI)
    "base_url": "https://api.anthropic.com/v1/messages",
}

PROVIDERS = ("anthropic", "openai_compatible")
# Endpoint prefilled in Settings when the user first switches to a local provider.
DEFAULT_OPENAI_BASE_URL = "http://localhost:11434/v1"   # Ollama

# A small, curated model menu for the settings UI (the field stays free-text).
MODEL_CHOICES = (
    "claude-haiku-4-5",    # fast + cheap: inline edits
    "claude-sonnet-5",     # balanced default
    "claude-opus-4-8",     # heavy reasoning
)


def config_dir(base: Optional[str] = None) -> str:
    """Return the per-user config directory (created lazily on save)."""
    if base is not None:
        return os.path.join(base, APP_DIR_NAME)
    root = os.environ.get("APPDATA")  # Windows
    if not root:
        root = os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(root, APP_DIR_NAME)


def config_path(base: Optional[str] = None) -> str:
    return os.path.join(config_dir(base), CONFIG_FILE)


def _coerce(key: str, value: Any) -> Any:
    """Coerce a stored value to the expected type, else fall back to default.

    Prevents a hand-edited config.json (e.g. "timeout": "120") from surfacing a
    raw TypeError deep in the client.
    """
    default = DEFAULTS[key]
    if key == "timeout":
        try:
            number = float(value)
            return number if number > 0 else default
        except (TypeError, ValueError):
            return default
    if value is None:
        return None
    if key == "temperature":
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    if key == "max_tokens":
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    # string-valued keys: provider, model, base_url
    return value if isinstance(value, str) else default


def load_config(base: Optional[str] = None) -> Dict[str, Any]:
    """Load settings merged over :data:`DEFAULTS`. Unknown keys are ignored,
    values are type-checked/coerced, and missing/corrupt files fall back to
    defaults (never raises)."""
    cfg = dict(DEFAULTS)
    try:
        with open(config_path(base), "r", encoding="utf-8") as handle:
            stored = json.load(handle)
    except (OSError, ValueError):
        return cfg
    if isinstance(stored, dict):
        for key in DEFAULTS:
            if key in stored:
                cfg[key] = _coerce(key, stored[key])
    return cfg


def save_config(cfg: Dict[str, Any], base: Optional[str] = None) -> str:
    """Persist only recognised keys; returns the path written."""
    directory = config_dir(base)
    os.makedirs(directory, exist_ok=True)
    path = config_path(base)
    to_store = {key: cfg.get(key, DEFAULTS[key]) for key in DEFAULTS}
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(to_store, handle, indent=2, sort_keys=True)
    return path


def client_kwargs(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Map a config dict onto ClaudeClient(**kwargs) (excluding the api key)."""
    return {
        "model": cfg.get("model") or DEFAULTS["model"],
        "base_url": cfg.get("base_url") or DEFAULTS["base_url"],
        "timeout": cfg.get("timeout") or DEFAULTS["timeout"],
    }
