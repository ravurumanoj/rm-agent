from __future__ import annotations

import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any

from app.constants import HISTORY_BLOCK_MAX_CONTENT_LEN


class InMemoryCheckpointer:
    """Process-local in-memory checkpoint store for orchestration state."""

    def __init__(self, *, max_sessions: int = 2000, max_turns_per_session: int = 50) -> None:
        self._max_sessions = max(1, max_sessions)
        self._max_turns_per_session = max(1, max_turns_per_session)
        self._snapshots: dict[str, dict[str, Any]] = {}
        self._history: dict[str, deque[dict[str, str]]] = {}
        self._session_order: deque[str] = deque()
        self._lock = threading.RLock()

    def _touch_session(self, session_id: str) -> None:
        if session_id in self._session_order:
            try:
                self._session_order.remove(session_id)
            except ValueError:
                pass
        self._session_order.append(session_id)

        while len(self._session_order) > self._max_sessions:
            evicted = self._session_order.popleft()
            self._snapshots.pop(evicted, None)
            self._history.pop(evicted, None)

    def save_snapshot(self, session_id: str, snapshot: dict[str, Any]) -> None:
        if not session_id.strip():
            return
        with self._lock:
            self._touch_session(session_id)
            self._snapshots[session_id] = {
                "session_id": session_id,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                **snapshot,
            }

    def load_snapshot(self, session_id: str) -> dict[str, Any]:
        if not session_id.strip():
            return {}
        with self._lock:
            value = self._snapshots.get(session_id) or {}
            return dict(value)

    def append_turn(self, session_id: str, *, user_message: str, assistant_message: str) -> None:
        if not session_id.strip():
            return
        with self._lock:
            self._touch_session(session_id)
            turns = self._history.setdefault(session_id, deque(maxlen=self._max_turns_per_session))
            turns.append({"role": "user", "content": user_message})
            turns.append({"role": "assistant", "content": assistant_message})

    def get_recent_history(self, session_id: str, max_turns: int = 3) -> list[dict[str, str]]:
        if not session_id.strip():
            return []
        with self._lock:
            turns = list(self._history.get(session_id, []))
        if not turns:
            return []
        if max_turns < 1:
            max_turns = 1
        return turns[-(max_turns * 2) :]

    def build_history_block(self, session_id: str, max_turns: int = 3) -> str:
        recent = self.get_recent_history(session_id, max_turns=max_turns)
        if not recent:
            return ""
        lines = ["Recent conversation:"]
        for entry in recent:
            role = "User" if entry.get("role") == "user" else "Assistant"
            content = str(entry.get("content", "")).strip().replace("\n", " ")
            if len(content) > HISTORY_BLOCK_MAX_CONTENT_LEN:
                content = content[: HISTORY_BLOCK_MAX_CONTENT_LEN - 3] + "..."
            lines.append(f"  {role}: {content}")
        return "\n".join(lines) + "\n\n"

    def clear_session(self, session_id: str) -> None:
        with self._lock:
            self._snapshots.pop(session_id, None)
            self._history.pop(session_id, None)
            try:
                self._session_order.remove(session_id)
            except ValueError:
                pass

    def reset_all(self) -> None:
        with self._lock:
            self._snapshots.clear()
            self._history.clear()
            self._session_order.clear()


in_memory_checkpointer = InMemoryCheckpointer()
