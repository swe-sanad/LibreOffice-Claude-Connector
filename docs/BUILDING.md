# Building the bundles

The connector ships as two installable bundles, both attached to every
[GitHub Release](../../releases):

| File | What it is | Installs into |
|------|-----------|---------------|
| `libreoffice-connector-<version>.mcpb` | MCP Bundle — the MCP server + its Python deps | Claude Desktop (Extensions) |
| `claude-connector-<version>.oxt` | LibreOffice extension — the in-app "Claude" menu | LibreOffice (Extension Manager) |

Most people just grab these from the release. If you'd rather build them
yourself — to audit exactly what you install — it takes one command each.

## Prerequisites

- **Python 3.8+** on `PATH`. That's it. The build scripts are **standard-library
  only** — no `pip install`, no `npm`, no LibreOffice needed to *build*.
  (Node and LibreOffice are only needed at *run* time, by the bundles.)
- Works on Windows, macOS, and Linux.

Everything below is run from the repository root.

## Build the MCP bundle (`.mcpb`)

```sh
python scripts/build_mcpb.py
# -> dist/libreoffice-connector-<version>.mcpb
```

It zips `manifest.json`, the Node launcher `index.js`, the MCP server
(`mcp/libreoffice_mcp.py`) and its helpers (`src/uno_bridge.py`,
`src/calc_actions.py`, `src/writer_actions.py`), the icon, `LICENSE`, and the
generated `docs/MCP-TOOLS.md`. Install by opening it in **Claude Desktop →
Settings → Extensions** (or your MCP client's bundle-install flow).

## Build the LibreOffice extension (`.oxt`)

```sh
python scripts/build_oxt.py
# -> dist/claude-connector-<version>.oxt
```

It assembles the `ext/` config (`Addons.xcu`, `description.xml`, `META-INF/…`,
icons) plus the registered components and the `claudeconn` helper package from
`src/`. Install with **LibreOffice → Tools → Extension Manager → Add**, or:

```sh
unopkg add dist/claude-connector-<version>.oxt
```

## Versioning

A single version string is shared by the server, the `.mcpb`, and the `.oxt`.
Keep them in lockstep with:

```sh
python scripts/stamp_version.py 0.11.0
```

which rewrites `SERVER_VERSION` (`mcp/libreoffice_mcp.py`), the `.mcpb`
`mcpb/manifest.json`, and the `.oxt` `ext/description.xml`. The release CI runs
this automatically with the **git tag** version, so a `v0.11.0` tag always
produces `…-0.11.0.mcpb` / `…-0.11.0.oxt` no matter what's committed.

## Verifying a downloaded release artifact

Both files are ordinary ZIPs — inspect them without installing:

```sh
unzip -l libreoffice-connector-<version>.mcpb
unzip -l claude-connector-<version>.oxt
```

To confirm a release matches this source, `stamp_version.py <that version>`,
rebuild, and compare the **file lists** (`unzip -l`). Note the archives are
*not* byte-identical to the release assets — ZIP entries carry build
timestamps — so compare contents, not checksums.

For an end-to-end check that the bundle actually launches (needs **Node** and
**LibreOffice** installed):

```sh
python scripts/test_mcpb_bundle.py          # handshake: initialize + tools/list
python scripts/test_mcpb_bundle.py --live    # also drives lo_status against LibreOffice
```

## Cutting a release (maintainer)

1. `python scripts/stamp_version.py X.Y.Z`
2. Update `CHANGELOG.md` (move `[Unreleased]` → `[X.Y.Z]`) and regenerate the
   tool reference: `python scripts/gen_mcp_tools_doc.py`.
3. Commit, then create the release + tag:
   `gh release create vX.Y.Z --notes-file <notes>` (or `--generate-notes`).
4. Pushing the tag triggers `.github/workflows/release.yml`, which builds the
   `.mcpb` + `.oxt` and attaches them to the release. Nothing else to do.

`dist/` is git-ignored — built bundles never get committed; they live only on
the release.
