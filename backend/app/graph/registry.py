from __future__ import annotations

from typing import Any, ClassVar, Dict, Type


class BaseNode:
    CATEGORY: ClassVar[str] = "bookcomet"
    RETURN_TYPES: ClassVar[tuple[str, ...]] = ()

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        raise NotImplementedError


NODE_CLASS_MAPPINGS: Dict[str, Type[BaseNode]] = {}


def register_node(name: str):
    def decorator(cls: Type[BaseNode]) -> Type[BaseNode]:
        NODE_CLASS_MAPPINGS[name] = cls
        return cls

    return decorator
