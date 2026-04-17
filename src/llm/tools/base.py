"""Base classes for Oracle 2.0 tool system."""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
import time

from pydantic import BaseModel


class ToolResult(BaseModel):
    success: bool
    data: Any
    metadata: Dict[str, Any] = {}
    error: Optional[str] = None
    execution_time: float = 0.0


class BaseTool(ABC):
    name: str = ""
    description: str = ""
    parameters: Dict[str, Any] = {}

    # Max chars returned in function response (chat history) to prevent context overflow.
    # Full data remains in result.data for post-loop synthesis if needed.
    HISTORY_MAX_CHARS: int = 8000

    def __init__(self, db, llm=None):
        self.db = db
        self.llm = llm

    def execute(self, **kwargs) -> ToolResult:
        start = time.time()
        try:
            result = self._execute(**kwargs)
            result.execution_time = time.time() - start
            return result
        except Exception as e:
            return ToolResult(
                success=False,
                data=None,
                error=str(e),
                execution_time=time.time() - start
            )

    @abstractmethod
    def _execute(self, **kwargs) -> ToolResult:
        """Subclasses implement this — raises on failure."""
        ...

    @abstractmethod
    def _format_success(self, data: Any, metadata: Dict) -> str:
        """Format successful result for LLM injection (full version)."""
        ...

    def format_for_llm(self, result: ToolResult) -> str:
        """Full format for final synthesis prompt."""
        if not result.success:
            return f"[TOOL FAILED: {result.error}]"
        return self._format_success(result.data, result.metadata)

    def format_for_history(self, result: ToolResult) -> str:
        """Compressed format for agentic loop function responses (max HISTORY_MAX_CHARS).

        Prevents chat history context overflow when tool results are large (e.g. RAGTool
        can return 60k+ chars). The LLM sees the key data and can decide its next step.
        Full data remains in result.data if needed for post-loop synthesis.
        """
        if not result.success:
            return f"[TOOL_ERROR: {result.error}]"
        full = self._format_success(result.data, result.metadata)
        if len(full) <= self.HISTORY_MAX_CHARS:
            return full
        omitted = len(full) - self.HISTORY_MAX_CHARS
        return full[:self.HISTORY_MAX_CHARS] + f"\n[... {omitted} chars omitted — data truncated for context efficiency]"

    @classmethod
    def _json_schema_to_genai_schema(cls, schema: Dict) -> Any:
        """Convert a JSON Schema dict to a genai.protos.Schema for Gemini function calling."""
        try:
            import google.generativeai as genai
            type_map = {
                "string": genai.protos.Type.STRING,
                "integer": genai.protos.Type.INTEGER,
                "number": genai.protos.Type.NUMBER,
                "boolean": genai.protos.Type.BOOLEAN,
                "object": genai.protos.Type.OBJECT,
                "array": genai.protos.Type.ARRAY,
            }
            schema_type = type_map.get(schema.get("type", "string"), genai.protos.Type.STRING)
            kwargs: Dict[str, Any] = {"type_": schema_type}

            if "description" in schema:
                kwargs["description"] = schema["description"]

            if "enum" in schema:
                kwargs["enum"] = [str(e) for e in schema["enum"]]

            if schema_type == genai.protos.Type.OBJECT:
                if "properties" in schema:
                    kwargs["properties"] = {
                        k: cls._json_schema_to_genai_schema(v)
                        for k, v in schema["properties"].items()
                    }
                if "required" in schema:
                    kwargs["required"] = list(schema["required"])

            if schema_type == genai.protos.Type.ARRAY and "items" in schema:
                kwargs["items"] = cls._json_schema_to_genai_schema(schema["items"])

            return genai.protos.Schema(**kwargs)
        except Exception:
            import google.generativeai as genai
            return genai.protos.Schema(type_=genai.protos.Type.STRING)

    @classmethod
    def to_function_declaration(cls) -> Any:
        """Build a genai.protos.FunctionDeclaration from this tool's class-level schema.

        Used by OracleOrchestrator to register tools with the Gemini model for native
        function calling. Only accesses class-level attributes (name, description, parameters).
        """
        import google.generativeai as genai
        params_schema = cls._json_schema_to_genai_schema(cls.parameters)
        return genai.protos.FunctionDeclaration(
            name=cls.name,
            description=cls.description,
            parameters=params_schema,
        )

    @classmethod
    def to_anthropic_tool(cls) -> Dict:
        """Build an Anthropic tool definition dict from this tool's class-level schema.

        Returns the format expected by Anthropic messages.create(tools=[...]):
            {"name": ..., "description": ..., "input_schema": {JSON Schema dict}}
        """
        return {
            "name": cls.name,
            "description": cls.description,
            "input_schema": cls.parameters,
        }
