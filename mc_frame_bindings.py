"""Access the running MC entry module from frame code with existing injection points."""

import sys
from types import ModuleType

# main initializes this under both import main and the application entry point.
app: ModuleType | None = None


def bind_entry(namespace: dict) -> None:
    global app
    if app is not None:
        return
    name = namespace["__name__"]
    spec = namespace.get("__spec__")
    if name == "main":
        # A normal import sets this flag; runpy executes a secondary module.
        if not getattr(spec, "_initializing", False):
            return
    elif name == "__main__":
        if spec is None:
            if namespace.get("__loader__") is None and not getattr(sys, "frozen", False):
                return
        elif spec.name != "main" or ("-m", "main") not in zip(sys.orig_argv, sys.orig_argv[1:]):
            return
    else:
        return
    module = sys.modules.get(name)
    if module is not None and vars(module) is namespace:
        app = module
