# Anthropic desktop-extension directory — submission dossier

Everything needed to file at **https://clau.de/desktop-extention-submission**.
Copy-paste the answers; the checklist at the bottom tracks the few steps only a
human (you) can do.

## Form answers

| Field | Answer |
|---|---|
| Extension name | LibreOffice Connector |
| Bundle | `libreoffice-connector-<version>.mcpb` from the latest release: https://github.com/swe-sanad/LibreOffice-Claude-Connector/releases/latest |
| One-line description | Drive LibreOffice from Claude: read/write Calc sheets, run macros, take window screenshots — 61 tools, auto-launches LibreOffice. |
| Long description | Connects Claude to a running (or auto-launched) LibreOffice via the UNO API. 61 tools: document lifecycle, Calc data/formulas/formatting/charts/validation, Writer text and tables, drawing shapes, embedded Basic macros (run/inspect/replace), saved-file XML inspection, real window screenshots, and a raw UNO escape hatch. Requires a local LibreOffice installation — the server runs under LibreOffice's own bundled Python, so there are no other dependencies and no telemetry. |
| Category | Productivity |
| Platforms | Windows (full), macOS/Linux (all tools except `lo_screenshot`) |
| Author / contact | Sanad Arousi · sanad.arousi@gmail.com |
| Homepage / repository | https://github.com/swe-sanad/LibreOffice-Claude-Connector |
| License | MPL-2.0 (open source) |
| Privacy policy URL | https://github.com/swe-sanad/LibreOffice-Claude-Connector/blob/master/docs/PRIVACY.md |
| Icon | `icon.png` (bundled in the .mcpb) |
| Test credentials | None needed — no accounts, no API keys. Reviewer needs a local LibreOffice install (free, libreoffice.org). |

## Reviewer test instructions (paste into the form)

1. Install LibreOffice (any 24.8+; default path is fine).
2. Install the `.mcpb`; when prompted, confirm the **LibreOffice bundled Python**
   path (Windows default `C:\Program Files\LibreOffice\program\python.exe`,
   macOS `/Applications/LibreOffice.app/Contents/Resources/python`).
3. No other setup: the server **auto-launches LibreOffice** on first tool use.
4. Smoke test: ask Claude — "create a new spreadsheet, write a 3×3 multiplication
   table into A1:C3, bold the first row, then take a screenshot of the window."
   Expected: LibreOffice opens, the data and formatting appear, and the
   screenshot tool returns a PNG path of the actual window.
5. `lo_status` reports the connection; `LO_AUTOSTART=0` disables auto-launch.

## Security notes for the review

- Local-only: the server talks to LibreOffice over localhost UNO; it makes **no
  network connections** (stdlib-only code, verifiable in the single-file server).
- The agent can do what the user can do in LibreOffice — including running
  document macros via `run_macro` and executing Python via `uno_exec`. These are
  first-class, documented tools (the escape hatch is the point of the product),
  surfaced to the model with clear descriptions so Claude requests them explicitly.
- No credentials handled; nothing written outside user-chosen paths and normal
  LibreOffice documents.

## Pre-flight checklist

- [x] `.mcpb` builds and installs (`scripts/build_mcpb.py`; v0.6.0 asset on the release)
- [x] `manifest.json` carries `privacy_policies`, icon, repository, license
- [x] Privacy policy published in-repo (HTTPS URL above)
- [x] Auto-launch verified from a cold start (no setup for the reviewer)
- [ ] **You**: verify the `.mcpb` installs in YOUR Claude Desktop (Settings ▸
      Extensions ▸ drag the file) and the smoke test passes end to end
- [ ] **You**: submit the form at https://clau.de/desktop-extention-submission
      (requires being signed in; directory currently targets Team/Enterprise)
- [ ] Optional polish before filing: a 60-second demo GIF in the README (reviewers
      love it), and GitHub Pages for a prettier privacy-policy URL
      (Settings ▸ Pages ▸ deploy from `master /docs`, then use
      `https://swe-sanad.github.io/LibreOffice-Claude-Connector/PRIVACY`)
