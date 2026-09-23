# SPDX-License-Identifier: MIT
"""Every global name a server function references must resolve.

Catches NameError-class bugs (a tool calling a helper that lives in another
module without importing it) across all tool modules, without LibreOffice:
import the server, then check each function's LOAD_GLOBAL names against its
module's globals and builtins. No UNO, no network.

What it checks: in libreoffice_mcp and every loconn module that importing it
loads, the functions defined at module level and the methods, staticmethods
and classmethods of module-level classes, with everything nested in them
(inner functions, lambdas, comprehensions, and the methods of classes defined
inside a function). A name passes if it is a module global, a builtin, or
assigned under ``global`` in one of those functions.

What it does not catch:
- code the walk never reaches: property getters and setters, methods of a
  class nested in another class, lambdas or functools.partial objects kept in
  a dict or other container, and the body of a decorated function when the
  decorator returns something else (a callable object, or a wrapper function,
  which is checked in its place);
- names resolved through an attribute (``os.missing``, ``self.missing``) or at
  run time (``getattr``, ``globals()[...]``, ``eval``);
- a name assigned under ``global`` but read before that assignment has run;
- modules outside the server package, such as src/uno_bridge.py;
- a name that resolves to the wrong object, or a call with the wrong
  arguments.
Module-level code and module-level class bodies run on import, so a NameError
there already fails the import; class bodies inside functions are not checked.
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
