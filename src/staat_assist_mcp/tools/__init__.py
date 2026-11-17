"""Tools. To add one, put a module with `@mcp.tool` functions anywhere under this package.

Modules are imported automatically, which registers their tools; there is no list to maintain.
Modules whose name starts with "_" are helpers and are skipped.
"""

import importlib
import pkgutil

for _module in pkgutil.walk_packages(__path__, prefix=f"{__name__}."):
    if not _module.name.rsplit(".", 1)[-1].startswith("_"):
        importlib.import_module(_module.name)
