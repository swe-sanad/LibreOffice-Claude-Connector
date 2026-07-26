# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Sanad Arousi
"""The tool registry.

Before this existed, every tool had to appear in FOUR places kept in step by
hand — the `TOOLS` dict, the `TOOL_DEFS` list, `_BASIC_TOOLS` and `_NO_UNDO` —
all of them global, all of them thousands of lines apart. Adding a tool and
forgetting one made it invisible, or silently gave a read-only tool an undo
context, and only a test caught it.

Now each tool module declares its own slice next to the code, and `register`
checks the four agree at IMPORT time: a schema with no handler, a handler with
no schema, or an unknown name in `basic`/`read_only` is an ImportError, not a
runtime surprise.
"""

# name -> callable
TOOLS = {}
# the JSON-RPC tools/list payload, in registration order
TOOL_DEFS = []
# names advertised in the everyday tier
BASIC_TOOLS = set()
# names that do NOT edit document content, so they get no undo context
NO_UNDO = set()


def register(namespace, defs, basic=(), read_only=()):
    """Register one module's tools.

    `namespace` is the module's globals(); each def's "name" is resolved to the
    `tool_<name>` callable in it. `basic` and `read_only` are name lists owned
    by that same module, so all four facts about a tool live together.
    """
    declared = {d["name"] for d in defs}

    for label, names in (("basic", basic), ("read_only", read_only)):
        unknown = set(names) - declared
        if unknown:
            raise ImportError("%s names tools this module does not define: %s"
                              % (label, ", ".join(sorted(unknown))))

    for definition in defs:
        name = definition["name"]
        if name in TOOLS:
            raise ImportError("duplicate tool %r" % name)
        func = namespace.get("tool_" + name)
        if func is None:
            raise ImportError(
                "tool %r has a schema but no tool_%s() in its module" % (name, name))
        TOOLS[name] = func
        TOOL_DEFS.append(definition)

    BASIC_TOOLS.update(basic)
    NO_UNDO.update(read_only)


def advertised(full):
    """tools/list payload: the everyday tier, or everything."""
    if full:
        return TOOL_DEFS
    return [d for d in TOOL_DEFS if d["name"] in BASIC_TOOLS]
