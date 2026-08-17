"""Node registry for workflow executor."""

from app.graph.nodes.handlers import NODE_HANDLERS

NODE_CLASS_MAPPINGS = NODE_HANDLERS

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_HANDLERS"]
