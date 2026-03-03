"""Base classes for Oracle 2.0 tool system."""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional
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
        """Format successful result for LLM injection."""
        ...

    def format_for_llm(self, result: ToolResult) -> str:
        if not result.success:
            return f"[TOOL FAILED: {result.error}]"
        return self._format_success(result.data, result.metadata)
