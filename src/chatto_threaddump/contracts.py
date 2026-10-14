from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DumpRequest:
    """One accepted command, independent of any other request."""

    room_id: str
    command_event_id: str
    thread_root_event_id: str
