# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Sanad Arousi
#
# Phase-0 probe round 4: is a per-shape entrance animation reliably scriptable?
#   powershell -File scripts/run_integration.ps1 -Test scripts/spike_impress2.py -Port 2003
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mcp.libreoffice_mcp as server  # noqa: E402


def main():
    desktop = server._desktop()
    doc = desktop.loadComponentFromURL("private:factory/simpress", "_blank", 0, ())
    try:
        page = doc.getDrawPages().getByIndex(0)
        page.Layout = 1
        title = page.getByIndex(0)

        print("=== animation root ===")
        root = page.AnimationNode
        print("  root type:", root.Type if hasattr(root, "Type") else "?",
              "hasChildren:", hasattr(root, "createEnumeration"))

        print("=== try building an appear effect via MainSequence ===")
        try:
            from com.sun.star.animations import AnimationNodeType
            # A high-level helper exists in some builds:
            # doc.createInstance("com.sun.star.presentation.CustomAnimationPreset")?
            for svc in ("com.sun.star.animations.ParallelTimeContainer",
                        "com.sun.star.animations.SequenceTimeContainer",
                        "com.sun.star.animations.Command",
                        "com.sun.star.animations.AnimateSet"):
                try:
                    obj = doc.createInstance(svc)
                    print("  createInstance", svc.rsplit(".", 1)[-1], "->", obj is not None)
                except Exception as exc:
                    print("  createInstance", svc.rsplit(".", 1)[-1], "FAILED:", exc)
        except Exception as exc:
            print("  import/setup FAILED:", exc)

        print("=== try the whole append-a-node dance (AnimateSet visibility) ===")
        try:
            from com.sun.star.animations.AnimationNodeType import PAR, SEQ
            main_seq = page.AnimationNode          # SequenceTimeContainer (root)
            par = doc.createInstance("com.sun.star.animations.ParallelTimeContainer")
            aset = doc.createInstance("com.sun.star.animations.AnimateSet")
            aset.Target = title
            aset.AttributeName = "Visibility"
            import uno
            aset.To = uno.Any("boolean", True)
            par.appendChild(aset)
            main_seq.appendChild(par)
            # count nodes now
            n = sum(1 for _ in main_seq.createEnumeration())
            print("  appended; main_seq child count:", n)
        except Exception as exc:
            print("  append dance FAILED:", type(exc).__name__, exc)
    finally:
        doc.close(False)


if __name__ == "__main__":
    main()
