# Privacy Policy — LibreOffice ↔ Claude Connector

*Effective: 2026-07-19 · Contact: sanad.arousi@gmail.com*

The LibreOffice ↔ Claude Connector consists of a **local MCP server** (drives
LibreOffice on your machine) and an optional **LibreOffice extension** (calls
Anthropic's Claude API from inside LibreOffice). This policy covers both.

## The short version

Everything runs **on your machine**. The project has **no servers, no accounts,
no telemetry, no analytics, and no data collection of any kind**.

## The MCP server (`libreoffice-connector`)

- Communicates only between your MCP client (e.g. Claude Desktop / Claude Code)
  and your local LibreOffice, over **localhost** (a local UNO pipe/socket). It
  never opens a connection to the internet.
- Document contents are read or modified only when the connected AI client
  invokes a tool, and go only to that client — which you configured and control.
  What that client (e.g. Claude) does with data you send it is governed by that
  client's own privacy policy (for Claude, see anthropic.com/legal/privacy).
- The `lo_screenshot` tool captures the LibreOffice window only, on explicit tool
  call, and writes the image to a local file path you can inspect.
- No usage data, crash reports, or identifiers are collected or transmitted by
  this project. The server writes logs only to its local stderr.

## The LibreOffice extension (`.oxt`, optional)

- Sends the text/cells **you explicitly select** (plus your instruction) directly
  to the **Anthropic API** over TLS when you invoke a Claude command. No other
  document content is transmitted, and nothing is sent without an explicit action.
- Your Anthropic API key is stored locally — on Windows encrypted at rest with
  DPAPI; it is never written to plaintext configuration or sent anywhere except
  the Anthropic API in the `x-api-key` header.

## Open source

The complete source is available at
https://github.com/swe-sanad/LibreOffice-Claude-Connector (MPL-2.0) — every claim
above is verifiable in code.

## Changes

Changes to this policy are made in the public repository with full git history.
