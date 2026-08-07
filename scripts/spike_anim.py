# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Sanad Arousi
#
# Prove per-object animations PERSIST: fresh minimal deck -> animate a shape via
# the shipped tool -> save .odp -> reopen -> the animation is still there.
#   powershell -File scripts/run_integration.ps1 -Test scripts/spike_anim.py -Port 2003
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mcp.libreoffice_mcp as server  # noqa: E402

ODP = os.path.join(os.environ.get("TEMP", "."), "anim_persist.odp")


def main():
    desktop = server._desktop()
    doc = desktop.loadComponentFromURL("private:factory/simpress", "_blank", 0, ())
    try:
        server.tool_impress_set_layout({"slide": 1, "layout": "title_content"})
        server.tool_impress_set_title({"slide": 1, "text": "Animated"})
        server.tool_impress_insert_shape({"slide": 1, "kind": "ellipse",
            "x_mm": 60, "y_mm": 90, "width_mm": 40, "height_mm": 30, "text": "hi"})
        server.tool_impress_add_animation({"slide": 1, "shape": 1,
            "effect": "appear", "trigger": "on_click"})
        server.tool_impress_add_animation({"slide": 1, "shape": 3,
            "effect": "fade", "trigger": "after_previous", "duration": 0.8})
        live = server._count_animations(doc.getDrawPages().getByIndex(0))
        print("live animations:", live)
        if os.path.exists(ODP):
            os.remove(ODP)
        doc.storeToURL(server._to_url(ODP), (server._pv("FilterName", "impress8"),))
        print("saved odp:", os.path.exists(ODP), os.path.getsize(ODP), "bytes")
        # reopen the saved file (leave the first doc open — closing it here has
        # been disposing the headless bridge) and recount from the file
        doc2 = desktop.loadComponentFromURL(server._to_url(ODP), "_blank", 0, ())
        after = server._count_animations(doc2.getDrawPages().getByIndex(0))
        print("animations after reload:", after)
        print("PERSIST OK" if after >= 2 else "PERSIST FAIL")
    finally:
        if os.path.exists(ODP):
            os.remove(ODP)


if __name__ == "__main__":
    main()
