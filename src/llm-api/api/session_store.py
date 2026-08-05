import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from langchain_core.messages import BaseMessage


@dataclass
class ConversationSession:
    messages: List[BaseMessage]
    citations: List[Dict[str, Any]]
    last_active: float


class SessionStore:
    """In-memory conversation store with TTL-based expiry.

    Single-process only: sessions live in a plain dict, so they don't survive
    a server restart and won't be shared across multiple uvicorn workers.
    """

    def __init__(self, ttl_seconds: float):
        self._ttl_seconds = ttl_seconds
        self._sessions: Dict[str, ConversationSession] = {}
        self._lock = threading.Lock()

    def create(self, messages: List[BaseMessage], citations: List[Dict[str, Any]]) -> str:
        conversation_id = uuid.uuid4().hex
        with self._lock:
            self._sessions[conversation_id] = ConversationSession(
                messages=messages, citations=citations, last_active=time.monotonic()
            )
        return conversation_id

    def get(self, conversation_id: str) -> Optional[ConversationSession]:
        with self._lock:
            session = self._sessions.get(conversation_id)
            if session is None:
                return None
            if time.monotonic() - session.last_active > self._ttl_seconds:
                del self._sessions[conversation_id]
                return None
            return session

    def append_messages(
        self,
        conversation_id: str,
        new_messages: List[BaseMessage],
        citations: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """Extend a session with this turn's full message trajectory (which may be more than
        one human/AI pair, e.g. tool-call rounds). Pass `citations` to replace the stored
        list -- only when the turn actually retrieved new evidence; omit to leave it as-is."""
        with self._lock:
            session = self._sessions.get(conversation_id)
            if session is None:
                raise KeyError(conversation_id)
            session.messages.extend(new_messages)
            if citations is not None:
                session.citations = citations
            session.last_active = time.monotonic()

    def sweep_expired(self) -> int:
        """Remove sessions that have been idle past the TTL. Returns count removed."""
        now = time.monotonic()
        with self._lock:
            expired = [cid for cid, session in self._sessions.items() if now - session.last_active > self._ttl_seconds]
            for cid in expired:
                del self._sessions[cid]
        return len(expired)

    def __len__(self) -> int:
        with self._lock:
            return len(self._sessions)
