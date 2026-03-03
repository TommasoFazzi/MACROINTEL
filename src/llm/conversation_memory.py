"""ConversationMemory — in-memory per-session conversation context."""

from collections import deque
from datetime import datetime
from typing import Any, Dict, List, Optional

from .schemas import QueryPlan
from ..utils.logger import get_logger

logger = get_logger(__name__)

# Follow-up detection keywords
FOLLOW_UP_PATTERNS = {
    "e ", "e invece", "e rispetto", "e riguardo", "anche ", "pure ",
    "invece ", "però ", "ma ", "allora ", "quindi ", "dunque ",
    "quello", "quella", "quelli", "quelle", "questo", "questa",
    "lo stesso", "la stessa", "il precedente", "la precedente",
    "di cui sopra", "menzionato", "citato", "appena detto",
}


class ConversationContext:
    """
    Per-session conversation buffer with entity tracking.

    Storage is in-memory only. TTL and cleanup are managed externally
    by OracleOrchestrator's background thread.
    """

    def __init__(self, session_id: str, max_buffer_size: int = 10):
        self.session_id = session_id
        self.messages: deque = deque(maxlen=max_buffer_size)
        self.entity_tracker: Dict[str, int] = {}
        self.last_query_plan: Optional[QueryPlan] = None
        self.message_count: int = 0
        self.created_at: datetime = datetime.now()

    def add_message(self, role: str, content: str, metadata: Optional[Dict] = None):
        self.messages.append({
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
            "metadata": metadata or {},
        })
        self.message_count += 1

    def get_context_for_llm(self) -> str:
        """Return last N messages formatted for LLM injection."""
        if not self.messages:
            return ""

        lines = ["[CONVERSAZIONE PRECEDENTE]"]
        for msg in self.messages:
            role_label = "UTENTE" if msg["role"] == "user" else "ORACLE"
            # Truncate long assistant responses to keep prompt manageable
            content = msg["content"]
            if msg["role"] == "assistant" and len(content) > 500:
                content = content[:500] + "..."
            lines.append(f"{role_label}: {content}")
        lines.append("[FINE CONVERSAZIONE PRECEDENTE]")

        return "\n".join(lines)

    def detect_follow_up(self, query: str) -> bool:
        """Heuristic: detect if query is a follow-up to previous exchange."""
        if not self.messages or self.message_count < 2:
            return False

        q_lower = query.lower().strip()

        # Very short queries in an active session are usually follow-ups
        if len(q_lower.split()) < 6 and self.message_count >= 2:
            return True

        # Check for follow-up keywords at start of query
        for pattern in FOLLOW_UP_PATTERNS:
            if q_lower.startswith(pattern):
                return True

        # Check for pronouns that reference prior entities
        pronouns = {"esso", "essa", "loro", "essi", "lui", "lei", "il", "la", "lo"}
        first_word = q_lower.split()[0] if q_lower.split() else ""
        if first_word in pronouns:
            return True

        return False

    def track_entities(self, entities: List[str]):
        """Increment mention count for each entity."""
        for entity in entities:
            self.entity_tracker[entity] = self.entity_tracker.get(entity, 0) + 1

    def get_top_entities(self, n: int = 5) -> List[str]:
        return sorted(self.entity_tracker, key=lambda e: self.entity_tracker[e], reverse=True)[:n]

    def clear(self):
        self.messages.clear()
        self.entity_tracker.clear()
        self.last_query_plan = None
        self.message_count = 0
