from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from chattolib._connect import ConnectError
from chattolib._pb.chatto.api.v1 import user_service_pb2
from chattolib._pb.chatto.realtime.v1 import realtime_pb2
from chattolib.exceptions import ChattoError
from chattolib.realtime import realtime_url
from google.protobuf.message import DecodeError
from websockets.asyncio.client import connect as WebSocketConnect
from websockets.exceptions import WebSocketException

RPC_TIMEOUT_SECONDS = 5


class ChattoOperationError(Exception):
    """A sanitized failure from a Chatto API operation."""


class ChattoStreamError(Exception):
    """A sanitized failure from the live Chatto event stream."""


class RealtimeConnect(WebSocketConnect):
    """A one-shot WebSocket connection that refuses all redirects."""

    def process_redirect(self, exc: Exception) -> Exception | str:
        return exc


@dataclass(frozen=True)
class MentionInput:
    """The trusted identifiers and text needed to handle one direct mention."""

    actor_id: str | None
    room_id: str
    event_id: str
    thread_root_event_id: str | None
    direct_mention_user_ids: tuple[str, ...]
    plain_text: str


async def live_mentions(
    client: Any, *, bot_user_id: str
) -> AsyncIterator[MentionInput]:
    """Yield new messages that directly mention this bot from the live boundary."""
    headers = (
        {"Cookie": f"chatto_session={client.session_cookie}"}
        if client.session_cookie
        else None
    )
    try:
        async with RealtimeConnect(
            realtime_url(client.base_url),
            additional_headers=headers,
            open_timeout=RPC_TIMEOUT_SECONDS,
        ) as websocket:
            realtime_proto: Any = realtime_pb2
            subscription = realtime_proto.RealtimeSubscribe(
                protocol_version=4,
                initial_state=realtime_proto.REALTIME_INITIAL_STATE_LIVE_ONLY,
            )
            if client.token:
                subscription.bearer_token = client.token
            await websocket.send(subscription.SerializeToString())

            async for raw in websocket:
                if isinstance(raw, str):
                    raise ChattoStreamError("Chatto sent an invalid realtime frame.")
                frame = realtime_proto.RealtimeServerFrame()
                frame.ParseFromString(raw)
                case = frame.WhichOneof("frame")
                if case in {"heartbeat", "caught_up"}:
                    continue
                if case == "close":
                    raise ChattoStreamError("Chatto closed the realtime stream.")
                if case == "snapshot":
                    raise ChattoStreamError(
                        "Chatto returned unexpected historical state."
                    )
                if case != "event":
                    raise ChattoStreamError("Chatto sent an invalid realtime frame.")

                event = frame.event
                if event.WhichOneof("event") != "message_posted":
                    continue
                payload = event.message_posted
                if event.actor_id == bot_user_id:
                    continue
                if payload.echo_of_event_id or payload.echo_from_thread_root_event_id:
                    continue

                direct_user_ids = tuple(
                    mention.direct.user_id
                    for mention in payload.mentions
                    if mention.WhichOneof("cause") == "direct"
                )
                if bot_user_id not in direct_user_ids:
                    continue

                yield MentionInput(
                    actor_id=event.actor_id or None,
                    room_id=payload.room_id,
                    event_id=event.id,
                    thread_root_event_id=payload.thread_root_event_id or None,
                    direct_mention_user_ids=direct_user_ids,
                    plain_text=payload.body_plaintext,
                )
            raise ChattoStreamError("Chatto realtime stream ended unexpectedly.")
    except asyncio.CancelledError:
        raise
    except ChattoStreamError:
        raise
    except ChattoError, DecodeError, OSError, TimeoutError, WebSocketException:
        raise ChattoStreamError("Chatto realtime stream failed.") from None


async def assigned_role_names(client: Any, *, user_id: str) -> frozenset[str]:
    """Read a user's stable assigned role names from the user directory."""
    if not user_id:
        raise ChattoOperationError("Could not look up requester roles.")

    headers = {"Authorization": f"Bearer {client.token}"} if client.token else {}
    user_service_proto: Any = user_service_pb2
    request = user_service_proto.GetUserRequest(user_id=user_id)
    try:
        async with asyncio.timeout(RPC_TIMEOUT_SECONDS):
            response = await client.services.user.get_user(request, headers=headers)
        if not response.HasField("user"):
            raise ChattoOperationError("Could not look up requester roles.")
        directory_member = response.user
        if not directory_member.HasField("user") or directory_member.user.id != user_id:
            raise ChattoOperationError("Could not look up requester roles.")
        return frozenset(directory_member.roles)
    except TimeoutError:
        raise ChattoOperationError("Requester role lookup timed out.") from None
    except ChattoOperationError:
        raise
    except ChattoError, ConnectError, DecodeError, OSError:
        raise ChattoOperationError("Could not look up requester roles.") from None
