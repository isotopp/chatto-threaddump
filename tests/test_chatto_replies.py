from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from chattolib import ChattoClient
from chattolib._pb.chatto.api.v1 import messages_pb2

from chatto_threaddump import chatto_replies
from chatto_threaddump.chatto_replies import reply_to_event

messages_proto: Any = messages_pb2


def test_reply_targets_command_event_without_selecting_placement() -> None:
    client = ChattoClient(token="fake-bot-key", base_url="https://chat.example.test")
    message_service = client.services.messages
    original_http = message_service._http
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        parsed = messages_proto.CreateMessageRequest()
        parsed.ParseFromString(request.content)
        assert parsed.room_id == "room-1"
        assert parsed.body == "The dump was accepted."
        assert parsed.in_reply_to == "command-event"
        assert not parsed.thread_root_event_id
        response = messages_proto.CreateMessageResponse()
        response.message.id = "reply-event"
        response.message.room_id = "room-1"
        return httpx.Response(200, content=response.SerializeToString())

    message_service._http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), timeout=5
    )

    async def run() -> None:
        await original_http.aclose()
        try:
            await reply_to_event(
                client,
                room_id="room-1",
                event_id="command-event",
                body="The dump was accepted.",
            )
        finally:
            await client.close()

    asyncio.run(run())
    assert len(requests) == 1
    assert str(requests[0].url) == (
        "https://chat.example.test/api/connect/chatto.api.v1.MessageService/CreateMessage"
    )
    assert requests[0].headers["Authorization"] == "Bearer fake-bot-key"


def test_reply_transport_errors_are_sanitized_and_not_retried() -> None:
    client = ChattoClient(token="fake-bot-key", base_url="https://chat.example.test")
    message_service = client.services.messages
    original_http = message_service._http
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

    message_service._http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), timeout=5
    )

    async def run() -> str:
        await original_http.aclose()
        try:
            await reply_to_event(
                client,
                room_id="room-1",
                event_id="command-event",
                body="The dump was accepted.",
            )
        except chatto_replies.ChattoReplyError as error:
            return str(error)
        finally:
            await client.close()
        raise AssertionError("failed reply unexpectedly succeeded")

    error = asyncio.run(run())
    assert error == "Could not send a Chatto reply."
    assert "private response body" not in error
    assert "fake secret" not in error
    assert attempts == 1


def test_reply_has_a_finite_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    class HangingClient:
        async def post_message(self, *_args: object, **_kwargs: object) -> None:
            await asyncio.Future()

    monkeypatch.setattr(chatto_replies, "RPC_TIMEOUT_SECONDS", 0.001)

    async def run() -> None:
        await reply_to_event(
            HangingClient(),
            room_id="room-1",
            event_id="command-event",
            body="Please wait.",
        )

    with pytest.raises(chatto_replies.ChattoReplyError, match="Chatto reply timed out"):
        asyncio.run(run())
