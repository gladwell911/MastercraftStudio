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
    if name == "main":
        # A normal import sets this flag; runpy executes a secondary module.
        if not getattr(namespace.get("__spec__"), "_initializing", False):
            return
    elif name != "__main__":
        return
    module = sys.modules.get(name)
    if module is not None and vars(module) is namespace:
        app = module
