from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from chattolib import ChattoClient
from chattolib._pb.chatto.api.v1 import (
    member_directory_pb2,
    user_service_pb2,
    users_pb2,
)
from chattolib._pb.chatto.realtime.v1 import realtime_pb2
from websockets.datastructures import Headers
from websockets.exceptions import InvalidStatus, WebSocketException
from websockets.http11 import Response

from chatto_threaddump import chatto_events

realtime_proto: Any = realtime_pb2
user_service_proto: Any = user_service_pb2
member_directory_proto: Any = member_directory_pb2
users_proto: Any = users_pb2

BOT_ID = "bot-user"


def _event(
    event_id: str,
    actor_id: str,
    *,
    room_id: str = "room-1",
    root_id: str = "thread-root",
    body: str = "dump this",
    direct_targets: tuple[str, ...] = (BOT_ID,),
    echo_of: str = "",
    echo_from_root: str = "",
) -> bytes:
    frame = realtime_proto.RealtimeServerFrame()
    event = frame.event
    event.id = event_id
    event.actor_id = actor_id
    payload = event.message_posted
    payload.room_id = room_id
    payload.thread_root_event_id = root_id
    payload.body_plaintext = body
    payload.echo_of_event_id = echo_of
    payload.echo_from_thread_root_event_id = echo_from_root
    for user_id in direct_targets:
        payload.mentions.add().direct.user_id = user_id
    return frame.SerializeToString()


def _patch_live_connection(monkeypatch: Any, frames: list[bytes]) -> dict[str, Any]:
    capture: dict[str, Any] = {"sent": []}

    class FakeWebSocket:
        async def send(self, data: bytes) -> None:
            capture["sent"].append(data)

        async def __aiter__(self):
            for frame in frames:
                yield frame

    class FakeConnect:
        def __init__(self, uri: str, **kwargs: Any) -> None:
            capture["uri"] = uri
            capture["kwargs"] = kwargs
            self.websocket = FakeWebSocket()

        async def __aenter__(self) -> FakeWebSocket:
            return self.websocket

        async def __aexit__(self, *_exc: object) -> None:
            return None

    monkeypatch.setattr(chatto_events, "RealtimeConnect", FakeConnect)
    return capture


def test_live_mentions_use_direct_targets_and_preserve_command_context(
    monkeypatch: Any,
) -> None:
    messages = [
        _event("event-direct", "requester", direct_targets=(BOT_ID, "other-user")),
        _event(
            "event-name-text",
            "requester",
            direct_targets=("other-user",),
            body="@dumpbot dump",
        ),
    ]
    call = _patch_live_connection(monkeypatch, messages)
    client = SimpleNamespace(
        base_url="https://chat.example.test",
        token="fake-bot-key",
        session_cookie=None,
    )

    async def run() -> list[chatto_events.MentionInput]:
        received = []
        try:
            async for message in chatto_events.live_mentions(
                client, bot_user_id=BOT_ID
            ):
                received.append(message)
        except chatto_events.ChattoStreamError as error:
            assert str(error) == "Chatto realtime stream ended unexpectedly."
        return received

    received = asyncio.run(run())

    assert call["uri"] == "wss://chat.example.test/api/realtime"
    assert call["kwargs"] == {"additional_headers": None, "open_timeout": 5}
    assert len(call["sent"]) == 1
    subscribe = realtime_proto.RealtimeSubscribe()
    subscribe.ParseFromString(call["sent"][0])
    assert subscribe.protocol_version == 4
    assert subscribe.initial_state == realtime_proto.REALTIME_INITIAL_STATE_LIVE_ONLY
    assert subscribe.bearer_token == "fake-bot-key"
    assert not subscribe.resume_cursor
    assert received == [
        chatto_events.MentionInput(
            actor_id="requester",
            room_id="room-1",
            event_id="event-direct",
            thread_root_event_id="thread-root",
            direct_mention_user_ids=(BOT_ID, "other-user"),
            plain_text="dump this",
        )
    ]


def test_live_mentions_ignore_bot_posts_and_channel_echoes(monkeypatch: Any) -> None:
    messages = [
        _event("event-self", BOT_ID),
        _event("event-echo", "requester", echo_of="source-event"),
        _event("event-thread-echo", "requester", echo_from_root="thread-root"),
    ]

    _patch_live_connection(monkeypatch, messages)
    client = SimpleNamespace(
        base_url="https://chat.example.test",
        token="fake-bot-key",
        session_cookie=None,
    )

    async def run() -> list[chatto_events.MentionInput]:
        received = []
        try:
            async for message in chatto_events.live_mentions(
                client, bot_user_id=BOT_ID
            ):
                received.append(message)
        except chatto_events.ChattoStreamError as error:
            assert str(error) == "Chatto realtime stream ended unexpectedly."
        return received

    assert asyncio.run(run()) == []


def test_realtime_connection_rejects_redirects() -> None:
    response = Response(
        302,
        "Found",
        Headers({"Location": "wss://other.example.test/api/realtime"}),
    )
    redirect = InvalidStatus(response)
    connection = chatto_events.RealtimeConnect("wss://chat.example.test/api/realtime")

    assert connection.process_redirect(redirect) is redirect


def test_realtime_connection_errors_are_sanitized(monkeypatch: Any) -> None:
    def fail_to_connect(*_args: object, **_kwargs: object) -> None:
        raise WebSocketException("private response body includes fake credential")

    monkeypatch.setattr(chatto_events, "RealtimeConnect", fail_to_connect)
    client = SimpleNamespace(
        base_url="https://chat.example.test",
        token="fake-bot-key",
        session_cookie=None,
    )

    async def run() -> None:
        async for _message in chatto_events.live_mentions(client, bot_user_id=BOT_ID):
            pass

    with pytest.raises(
        chatto_events.ChattoStreamError, match="Chatto realtime stream failed"
    ):
        asyncio.run(run())


def test_assigned_role_names_come_from_user_directory_connect_rpc() -> None:
    client = ChattoClient(token="fake-bot-key", base_url="https://chat.example.test")
    user_service = client.services.user
    original_http = user_service._http
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        parsed = user_service_proto.GetUserRequest()
        parsed.ParseFromString(request.content)
        assert parsed.user_id == "requester-7"
        response = user_service_proto.GetUserResponse(
            user=member_directory_proto.DirectoryMember(
                user=users_proto.User(id="requester-7"),
                roles=["thread-exporter", "viewer"],
            )
        )
        return httpx.Response(200, content=response.SerializeToString())

    user_service._http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), timeout=5
    )

    async def run() -> frozenset[str]:
        await original_http.aclose()
        try:
            return await chatto_events.assigned_role_names(
                client, user_id="requester-7"
            )
        finally:
            await client.close()

    assert asyncio.run(run()) == frozenset({"thread-exporter", "viewer"})
    assert len(requests) == 1
    assert str(requests[0].url) == (
        "https://chat.example.test/api/connect/chatto.api.v1.UserService/GetUser"
    )
    assert requests[0].headers["Authorization"] == "Bearer fake-bot-key"


def test_role_lookup_fails_closed_when_directory_identity_mismatches() -> None:
    client = ChattoClient(token="fake-bot-key", base_url="https://chat.example.test")
    user_service = client.services.user
    original_http = user_service._http

    def handler(_request: httpx.Request) -> httpx.Response:
        response = user_service_proto.GetUserResponse(
            user=member_directory_proto.DirectoryMember(
                user=users_proto.User(id="somebody-else"),
                roles=["thread-exporter"],
            )
        )
        return httpx.Response(200, content=response.SerializeToString())

    user_service._http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), timeout=5
    )

    async def run() -> None:
        await original_http.aclose()
        try:
            await chatto_events.assigned_role_names(client, user_id="requester-7")
        finally:
            await client.close()

    with pytest.raises(
        chatto_events.ChattoOperationError,
        match="Could not look up requester roles",
    ):
        asyncio.run(run())


def test_role_lookup_errors_are_sanitized_and_not_retried() -> None:
    client = ChattoClient(token="fake-bot-key", base_url="https://chat.example.test")
    user_service = client.services.user
    original_http = user_service._http
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            500,
            json={
                "code": "internal",
                "message": "private response body and fake secret",
            },
        )

    user_service._http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), timeout=5
    )

    async def run() -> str:
        await original_http.aclose()
        try:
            await chatto_events.assigned_role_names(client, user_id="requester-7")
        except chatto_events.ChattoOperationError as error:
            return str(error)
        finally:
            await client.close()
        raise AssertionError("failed role lookup unexpectedly succeeded")

    error = asyncio.run(run())
    assert error == "Could not look up requester roles."
    assert "private response body" not in error
    assert "fake secret" not in error
    assert attempts == 1
