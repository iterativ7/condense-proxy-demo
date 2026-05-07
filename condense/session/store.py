"""Session state storage for budget tracking."""

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class SessionState:
    """State for a single session."""
    session_id: str
    total_cost_usd: float = 0.0
    turn_count: int = 0
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    # Recent request hashes for loop detection
    recent_request_hashes: deque = field(default_factory=lambda: deque(maxlen=20))


class SessionStore:
    """In-memory session state storage."""

    def __init__(self):
        self._sessions: Dict[str, SessionState] = {}

    async def get(self, session_id: str) -> Optional[SessionState]:
        """Get session state by ID."""
        return self._sessions.get(session_id)

    async def get_or_create(self, session_id: str) -> SessionState:
        """Get existing session or create a new one."""
        if session_id not in self._sessions:
            self._sessions[session_id] = SessionState(session_id=session_id)
        return self._sessions[session_id]

    async def update(self, session_id: str, cost_usd: float, request_hash: str) -> SessionState:
        """Update session with new request data."""
        session = await self.get_or_create(session_id)
        session.total_cost_usd += cost_usd
        session.turn_count += 1
        session.last_active = time.time()
        session.recent_request_hashes.append(request_hash)
        return session

    async def size(self) -> int:
        return len(self._sessions)

    async def clear(self) -> None:
        self._sessions.clear()
