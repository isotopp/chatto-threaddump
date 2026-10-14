from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from typing import Any

import httpx
import pytest
from chattolib import ChattoClient
from chattolib._pb.chatto.api.v1 import (
    member_directory_pb2,
    messages_pb2,
    user_service_pb2,
    users_pb2,
)

from chatto_threaddump.admission import handle_mention
from chatto_threaddump.chatto_events import MentionInput
from chatto_threaddump.contracts import DumpRequest
from chatto_threaddump.inbox import RequestInbox

member_directory_proto: Any = member_directory_pb2
messages_proto: Any = messages_pb2
user_service_proto: Any = user_service_pb2
users_proto: Any = users_pb2


async def _install_fake_transport(
    client: ChattoClient,
    *,
    roles: list[str],
    role_response_status: int = 200,
    reply_response_status: int = 200,
) -> tuple[list[httpx.Request], list[str]]:
    requests: list[httpx.Request] = []
    replies: list[str] = []
    user_service = client.services.user
    message_service = client.services.messages
    original_user_http = user_service._http
    original_message_http = message_service._http

    def user_handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if role_response_status != 200:
            return httpx.Response(
                role_response_status,
                json={"message": "private response body fake credential"},
            )
        parsed = user_service_proto.GetUserRequest()
        parsed.ParseFromString(request.content)
        response = user_service_proto.GetUserResponse(
            user=member_directory_proto.DirectoryMember(
                user=users_proto.User(id=parsed.user_id), roles=roles
            )
        )
        return httpx.Response(200, content=response.SerializeToString())

    def message_handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        parsed = messages_proto.CreateMessageRequest()
        parsed.ParseFromString(request.content)
        replies.append(parsed.body)
        if reply_response_status != 200:
            return httpx.Response(
                reply_response_status,
                json={"message": "private response body fake credential"},
            )
        response = messages_proto.CreateMessageResponse()
        response.message.id = "reply-event"
        response.message.room_id = parsed.room_id
        return httpx.Response(200, content=response.SerializeToString())

    user_service._http = httpx.AsyncClient(
        transport=httpx.MockTransport(user_handler), timeout=5
    )
    message_service._http = httpx.AsyncClient(
        transport=httpx.MockTransport(message_handler), timeout=5
    )
    await original_user_http.aclose()
    await original_message_http.aclose()
    return requests, replies


def _mention(
    *,
    text: str = "Please DuMp this thread.",
    actor_id: str | None = "requester-1",
    root_id: str | None = "thread-root",
    direct_targets: tuple[str, ...] = ("bot-user", "other-user"),
    event_id: str = "command-event",
) -> MentionInput:
    return MentionInput(
        actor_id=actor_id,
        room_id="room-1",
        event_id=event_id,
        thread_root_event_id=root_id,
        direct_mention_user_ids=direct_targets,
        plain_text=text,
    )


def _run_mentions(
    mentions: Sequence[MentionInput],
    *,
    roles: list[str],
    role_response_status: int = 200,
    reply_response_status: int = 200,
    admit_request: Callable[[DumpRequest], bool] | None = None,
) -> tuple[list[httpx.Request], list[str], list[DumpRequest]]:
    client = ChattoClient(token="fake-bot-key", base_url="https://chat.example.test")
    accepted: list[DumpRequest] = []

    def capture(request: DumpRequest) -> bool:
        accepted.append(request)
        return True

    async def scenario() -> tuple[list[httpx.Request], list[str]]:
        requests, replies = await _install_fake_transport(
            client,
            roles=roles,
            role_response_status=role_response_status,
            reply_response_status=reply_response_status,
        )
        try:
            for mention in mentions:
                await handle_mention(
                    client,
                    mention,
                    bot_user_id="bot-user",
                    required_role_name="thread-exporter",
                    admit_request=(
                        admit_request if admit_request is not None else capture
                    ),
                )
        finally:
            await client.close()
        return requests, replies

    requests, replies = asyncio.run(scenario())
    return requests, replies, accepted


def test_authorized_thread_command_is_queued_and_acknowledged() -> None:
    client = ChattoClient(token="fake-bot-key", base_url="https://chat.example.test")

    async def scenario() -> tuple[list[httpx.Request], list[str], list[DumpRequest]]:
        requests, replies = await _install_fake_transport(
            client, roles=["thread-exporter"]
        )
        inbox = RequestInbox(capacity=1)
        received: list[DumpRequest] = []
        arrived = asyncio.Event()

        async def process(request: DumpRequest) -> None:
            received.append(request)
            arrived.set()

        consumer = asyncio.create_task(inbox.run(process))
        try:
            await handle_mention(
                client,
                _mention(),
                bot_user_id="bot-user",
                required_role_name="thread-exporter",
                admit_request=inbox.admit,
            )
            await arrived.wait()
        finally:
            consumer.cancel()
            try:
                await consumer
            except asyncio.CancelledError:
                pass
            await client.close()

        return requests, replies, received

    requests, replies, received = asyncio.run(scenario())

    assert replies == ["The dump was accepted."]
    assert received == [
        DumpRequest(
            room_id="room-1",
            command_event_id="command-event",
            thread_root_event_id="thread-root",
        )
    ]
    assert len(requests) == 2
    posted_reply = messages_proto.CreateMessageRequest()
    posted_reply.ParseFromString(requests[1].content)
    assert posted_reply.in_reply_to == "command-event"
    assert not posted_reply.thread_root_event_id


def test_direct_mention_without_whole_dump_word_gets_help_without_authorization() -> (
    None
):
    requests, replies, accepted = _run_mentions(
        [_mention(text="@bot dumpingprices")], roles=[]
    )

    assert replies == [
        (
            "To dump a thread you need to be in a thread, and your message must include "
            "the word dump."
        )
    ]
    assert accepted == []
    assert len(requests) == 1
    posted_reply = messages_proto.CreateMessageRequest()
    posted_reply.ParseFromString(requests[0].content)
    assert posted_reply.in_reply_to == "command-event"


def test_whole_dump_word_accepts_case_and_punctuation_variants() -> None:
    mentions = [
        _mention(text=text, event_id=event_id)
        for event_id, text in (
            ("command-1", "dump"),
            ("command-2", "DuMp,"),
            ("command-3", "DUMP!"),
        )
    ]
    requests, replies, accepted = _run_mentions(mentions, roles=["thread-exporter"])

    assert replies == ["The dump was accepted."] * 3
    assert len(requests) == 6
    assert [request.command_event_id for request in accepted] == [
        "command-1",
        "command-2",
        "command-3",
    ]


def test_message_that_does_not_directly_mention_this_bot_is_ignored() -> None:
    requests, replies, accepted = _run_mentions(
        [_mention(direct_targets=("other-user",))], roles=["thread-exporter"]
    )

    assert requests == []
    assert replies == []
    assert accepted == []


def test_role_denial_precedes_top_level_error() -> None:
    requests, replies, accepted = _run_mentions(
        [_mention(root_id=None)], roles=["viewer"]
    )

    assert replies == ["You do not have the required role thread-exporter."]
    assert accepted == []
    assert len(requests) == 2
    assert "GetUser" in str(requests[0].url)
    posted_reply = messages_proto.CreateMessageRequest()
    posted_reply.ParseFromString(requests[1].content)
    assert posted_reply.in_reply_to == "command-event"


def test_authorized_top_level_command_is_not_queued() -> None:
    requests, replies, accepted = _run_mentions(
        [_mention(root_id=None)], roles=["thread-exporter"]
    )

    assert replies == ["This only works inside a thread, not at the top level."]
    assert accepted == []
    assert len(requests) == 2
    posted_reply = messages_proto.CreateMessageRequest()
    posted_reply.ParseFromString(requests[1].content)
    assert posted_reply.in_reply_to == "command-event"


def test_role_lookup_failure_fails_closed_with_sanitized_reply() -> None:
    requests, replies, accepted = _run_mentions(
        [_mention()], roles=[], role_response_status=500
    )

    assert replies == [
        "I could not verify your role, so the thread dump request was not accepted."
    ]
    assert accepted == []
    assert len(requests) == 2
    assert "private response body" not in replies[0]
    assert "fake credential" not in replies[0]


def test_full_inbox_replies_without_accepting_the_command() -> None:
    client = ChattoClient(token="fake-bot-key", base_url="https://chat.example.test")

    async def scenario() -> tuple[list[httpx.Request], list[str], list[DumpRequest]]:
        requests, replies = await _install_fake_transport(
            client, roles=["thread-exporter"]
        )
        inbox = RequestInbox(capacity=1)
        previous_request = DumpRequest(
            room_id="room-1",
            command_event_id="prior-command",
            thread_root_event_id="thread-root",
        )
        assert inbox.admit(previous_request)
        try:
            await handle_mention(
                client,
                _mention(),
                bot_user_id="bot-user",
                required_role_name="thread-exporter",
                admit_request=inbox.admit,
            )
            processed: list[DumpRequest] = []
            received = asyncio.Event()

            async def process(request: DumpRequest) -> None:
                processed.append(request)
                received.set()

            consumer = asyncio.create_task(inbox.run(process))
            try:
                await received.wait()
            finally:
                consumer.cancel()
                try:
                    await consumer
                except asyncio.CancelledError:
                    pass
        finally:
            await client.close()
        return requests, replies, processed

    requests, replies, processed = asyncio.run(scenario())

    assert replies == ["The thread dump queue is full. Please try again later."]
    assert processed == [
        DumpRequest(
            room_id="room-1",
            command_event_id="prior-command",
            thread_root_event_id="thread-root",
        )
    ]
    assert len(requests) == 2


def test_failed_acceptance_reply_is_logged_once_and_keeps_accepted_request(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = ChattoClient(token="fake-bot-key", base_url="https://chat.example.test")

    async def scenario() -> tuple[list[httpx.Request], list[str], list[DumpRequest]]:
        requests, replies = await _install_fake_transport(
            client, roles=["thread-exporter"], reply_response_status=500
        )
        inbox = RequestInbox(capacity=1)
        try:
            await handle_mention(
                client,
                _mention(),
                bot_user_id="bot-user",
                required_role_name="thread-exporter",
                admit_request=inbox.admit,
            )

            processed: list[DumpRequest] = []
            received = asyncio.Event()

            async def process(request: DumpRequest) -> None:
                processed.append(request)
                received.set()

            consumer = asyncio.create_task(inbox.run(process))
            try:
                await received.wait()
            finally:
                consumer.cancel()
                try:
                    await consumer
                except asyncio.CancelledError:
                    pass
        finally:
            await client.close()
        return requests, replies, processed

    caplog.set_level("WARNING", logger="chatto_threaddump.admission")
    requests, replies, processed = asyncio.run(scenario())

    assert replies == ["The dump was accepted."]
    assert caplog.messages == [
        "Could not acknowledge accepted thread dump request: Could not send a Chatto reply."
    ]
    assert "private response body" not in caplog.text
    assert "fake credential" not in caplog.text
    assert processed == [
        DumpRequest(
            room_id="room-1",
            command_event_id="command-event",
            thread_root_event_id="thread-root",
        )
    ]
    assert len(requests) == 2
