"""In-process scrape session tracking.

Deliberately NOT persisted to Postgres — no new table, no schema change.
A scrape session is just "which candidate ids did this browser tab's
Start click produce", which the web app itself is the only thing that
needs to know. Lost on process restart, same tradeoff the CLI already
accepts by not persisting anything about its own invocations either.
Appropriate for a local single-user MVP tool; would need a real store
(Redis, a DB table) if this ever needs to survive restarts or serve
multiple concurrent processes.
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Dict, List, Optional

_lock = threading.Lock()
_sessions: Dict[str, dict] = {}


def create_session(urls: List[str]) -> str:
    session_id = uuid.uuid4().hex[:12]
    with _lock:
        _sessions[session_id] = {
            "id": session_id,
            "urls": urls,
            "candidate_ids": [],
            "status": "starting",  # starting -> discovering -> crawling -> verifying -> done -> error
            "created_at": time.time(),
            "error": None,
            "crawl_results": {},  # candidate_id -> outcome string
        }
    return session_id


def get_session(session_id: str) -> Optional[dict]:
    with _lock:
        session = _sessions.get(session_id)
        return dict(session) if session else None


def update_session(session_id: str, **kwargs) -> None:
    with _lock:
        if session_id in _sessions:
            _sessions[session_id].update(kwargs)


def set_candidate_result(session_id: str, candidate_id: int, result: str) -> None:
    with _lock:
        if session_id in _sessions:
            _sessions[session_id]["crawl_results"][candidate_id] = result
