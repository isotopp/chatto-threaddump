from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Any

from chatto_threaddump.chatto_events import (
    ChattoOperationError,
    MentionInput,
    assigned_role_names,
)
from chatto_threaddump.chatto_replies import ChattoReplyError, reply_to_event
from chatto_threaddump.contracts import DumpRequest

HELP_REPLY = (
    "To dump a thread you need to be in a thread, and your message must include "
    "the word dump."
)
THREAD_ONLY_REPLY = "This only works inside a thread, not at the top level."
QUEUE_FULL_REPLY = "The thread dump queue is full. Please try again later."
ACCEPTED_REPLY = "The dump was accepted."
ROLE_LOOKUP_FAILED_REPLY = (
    "I could not verify your role, so the thread dump request was not accepted."
)

_DUMP_WORD = re.compile(r"\bdump\b", re.IGNORECASE)
_LOGGER = logging.getLogger(__name__)


async def handle_mention(
    client: Any,
    mention: MentionInput,
    *,
    bot_user_id: str,
    required_role_name: str,
    admit_request: Callable[[DumpRequest], bool],
) -> None:
    """Reply to a direct mention and enqueue eligible thread dump commands."""
    if bot_user_id not in mention.direct_mention_user_ids:
        return

    if not _DUMP_WORD.search(mention.plain_text):
        await _reply(client, mention, HELP_REPLY)
        return

    try:
        role_names = await assigned_role_names(client, user_id=mention.actor_id or "")
    except ChattoOperationError:
        await _reply(client, mention, ROLE_LOOKUP_FAILED_REPLY)
        return

    if required_role_name not in role_names:
        await _reply(
            client,
            mention,
            f"You do not have the required role {required_role_name}.",
        )
        return

    if not mention.thread_root_event_id:
        await _reply(client, mention, THREAD_ONLY_REPLY)
        return

    request = DumpRequest(
        room_id=mention.room_id,
        command_event_id=mention.event_id,
        thread_root_event_id=mention.thread_root_event_id,
    )
    if not admit_request(request):
        await _reply(client, mention, QUEUE_FULL_REPLY)
        return

    # The request is already accepted; a reply failure must not undo admission.
    try:
        await _reply(client, mention, ACCEPTED_REPLY)
    except ChattoReplyError as error:
        _LOGGER.warning("Could not acknowledge accepted thread dump request: %s", error)


async def _reply(client: Any, mention: MentionInput, body: str) -> None:
    await reply_to_event(
        client,
        room_id=mention.room_id,
        event_id=mention.event_id,
        body=body,
    )
