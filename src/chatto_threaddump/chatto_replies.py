from __future__ import annotations

import asyncio
from typing import Any

from chattolib.exceptions import ChattoError
from google.protobuf.message import DecodeError

RPC_TIMEOUT_SECONDS = 5


class ChattoReplyError(Exception):
    """A sanitized failure from the Chatto reply operation."""


async def reply_to_event(
    client: Any,
    *,
    room_id: str,
    event_id: str,
    body: str,
) -> None:
    """Reply to the command event and let Chatto choose where the reply appears."""
    try:
        async with asyncio.timeout(RPC_TIMEOUT_SECONDS):
            await client.post_message(room_id, body, in_reply_to=event_id)
    except TimeoutError:
        raise ChattoReplyError("Chatto reply timed out.") from None
    except AssertionError, ChattoError, DecodeError, OSError:
        raise ChattoReplyError("Could not send a Chatto reply.") from None
