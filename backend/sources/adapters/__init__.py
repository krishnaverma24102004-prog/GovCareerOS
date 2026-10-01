"""Auto-discovery of source listing adapters (see base.py for the contract)."""
import importlib
import pkgutil
from types import ModuleType
from typing import Dict, Optional

def all_adapters() -> Dict[str, ModuleType]:
    found: Dict[str, ModuleType] = {}
    for info in pkgutil.iter_modules(__path__):
        if info.name.startswith("_") or info.name == "base":
            continue
        module = importlib.import_module(f"{__name__}.{info.name}")
        source = getattr(module, "SOURCE", None)
        if source and callable(getattr(module, "parse_listing", None)):
            found[source] = module
    return found

def adapter_for(source_name: str) -> Optional[ModuleType]:
    return all_adapters().get(source_name)
