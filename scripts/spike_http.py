# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Live HTTPS smoke test — proves TLS + connectivity + auth from *this* Python.

Run it with LibreOffice's BUNDLED interpreter to prove the connector can reach
Claude from inside the office's Python environment:

    & "C:\\Program Files\\LibreOffice\\program\\python.exe" scripts\\spike_http.py

Requires the ANTHROPIC_API_KEY environment variable. Exits non-zero on failure.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import claude_client as cc


def main() -> int:
    print("Python:", sys.version.replace("\n", " "))
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        print("SKIP: ANTHROPIC_API_KEY is not set. "
              "Set it (setx ANTHROPIC_API_KEY \"sk-ant-...\") and re-run.")
        return 2

    client = cc.ClaudeClient(api_key=key, model="claude-haiku-4-5")
    try:
        result = client.send(
            system="You are a terse assistant. Reply with a single short sentence.",
            prompt="In one sentence, confirm you can hear me.",
            max_tokens=64,
        )
    except cc.ClaudeError as exc:
        print("FAIL:", type(exc).__name__, "-", exc)
        return 1

    print("OK   model:", result.model)
    print("OK   stop_reason:", result.stop_reason)
    print("OK   usage:", result.usage)
    print("OK   reply:", result.text.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
