# SPDX-License-Identifier: MIT
"""Every global name a server function references must resolve.

Catches NameError-class bugs (a tool calling a helper that lives in another
module without importing it) across all tool modules, without LibreOffice:
import the server, then check each function's LOAD_GLOBAL names against its
module's globals and builtins. No UNO, no network.
"""

import builtins
import dis
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mcp"))

import libreoffice_mcp  # noqa: E402,F401 - imports and registers every tool module


def _codes(code):
    yield code
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            yield from _codes(const)


def _functions(module):
    for obj in list(vars(module).values()):
        if isinstance(obj, types.FunctionType) and obj.__module__ == module.__name__:
            yield obj
        elif isinstance(obj, type) and obj.__module__ == module.__name__:
            for member in vars(obj).values():
                func = getattr(member, "__func__", member)
                if isinstance(func, types.FunctionType):
                    yield func


class TestGlobalNamesResolve(unittest.TestCase):
    def test_every_global_reference_resolves(self):
        modules = [m for n, m in sorted(sys.modules.items())
                   if m is not None and (n == "libreoffice_mcp" or n.startswith("loconn"))]
        self.assertGreater(len(modules), 5, "tool modules were not imported")
        missing = []
        for module in modules:
            namespace = vars(module)
            funcs = list(_functions(module))
            assigned = {ins.argval for f in funcs for code in _codes(f.__code__)
                        for ins in dis.get_instructions(code)
                        if ins.opname in ("STORE_GLOBAL", "DELETE_GLOBAL")}
            for func in funcs:
                for code in _codes(func.__code__):
                    for ins in dis.get_instructions(code):
                        name = ins.argval
                        if (ins.opname == "LOAD_GLOBAL" and name not in namespace
                                and name not in assigned and not hasattr(builtins, name)):
                            missing.append("%s.%s -> %s" % (
                                module.__name__, func.__qualname__, name))
        self.assertEqual(sorted(set(missing)), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
