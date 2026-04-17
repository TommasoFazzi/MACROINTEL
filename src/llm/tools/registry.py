"""ToolRegistry — manages tool classes and instantiation."""

from typing import Dict, List, Tuple, Any

from .base import BaseTool


class ToolRegistry:
    def __init__(self):
        self._tools: Dict[str, Tuple[type, Dict[str, Any]]] = {}

    def register(self, tool_class: type, **init_kwargs):
        self._tools[tool_class.name] = (tool_class, init_kwargs)

    def get_tool(self, name: str) -> BaseTool:
        if name not in self._tools:
            raise KeyError(f"Tool '{name}' not registered. Available: {list(self._tools.keys())}")
        tool_class, init_kwargs = self._tools[name]
        return tool_class(**init_kwargs)

    def list_tools(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": tool_class.name,
                "description": tool_class.description,
                "parameters": tool_class.parameters,
            }
            for tool_class, _ in self._tools.values()
        ]

    def registered_names(self) -> List[str]:
        return list(self._tools.keys())

    def get_function_declarations(self) -> List:
        """Return genai.protos.FunctionDeclaration for all registered tools.

        Used by OracleOrchestrator to build the Gemini function-calling model.
        Accesses class-level schema attrs — no db/llm needed for this step.
        """
        return [tool_class.to_function_declaration() for tool_class, _ in self._tools.values()]

    def get_anthropic_tools(self) -> List[Dict]:
        """Return Anthropic tool definitions for all registered tools.

        Used by OracleOrchestrator (Claude) to pass tools to messages.create().
        """
        return [tool_class.to_anthropic_tool() for tool_class, _ in self._tools.values()]
