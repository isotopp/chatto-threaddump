import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from threading import enumerate as active_threads
from threading import get_ident

import pytest

from chatto_threaddump.chatto_events import ChattoStreamError, MentionInput
from chatto_threaddump.config import Settings
from chatto_threaddump.contracts import DumpRequest
from chatto_threaddump.runtime import BotRuntimeError, run_bot


@dataclass
class _Profile:
    id: str


class _Client:
    def __init__(self) -> None:
        self.closed = False

    async def me(self) -> _Profile:
        return _Profile(id="bot-user-id")


class _BrokenClient(_Client):
    async def me(self) -> _Profile:
        raise RuntimeError("private response and fake-key")


def _settings(tmp_path) -> Settings:
    return Settings(
        server_url="https://chat.example.test",
        api_key="fake-key",
        role_name="thread-exporter",
        queue_length=2,
        temp_dir=tmp_path,
    )


def _mention(event_id: str) -> MentionInput:
    return MentionInput(
        actor_id="requester",
        room_id="room",
        event_id=event_id,
        thread_root_event_id="root",
        direct_mention_user_ids=("bot-user-id",),
        plain_text="dump this",
    )


def test_runtime_uses_authenticated_bot_identity_and_sanitizes_stream_failure(
    tmp_path,
) -> None:
    async def scenario() -> None:
        client = _Client()
        observed: list[tuple[str, str, str]] = []

        @asynccontextmanager
        async def client_factory(**kwargs):
            assert kwargs == {
                "token": "fake-key",
                "base_url": "https://chat.example.test",
            }
            try:
                yield client
            finally:
                client.closed = True

        async def event_stream(_client, *, bot_user_id):
            observed.append(("stream", bot_user_id, ""))
            yield MentionInput(
                actor_id="requester",
                room_id="room",
                event_id="command",
                thread_root_event_id="root",
                direct_mention_user_ids=(bot_user_id,),
                plain_text="dump this",
            )
            raise ChattoStreamError("private response and fake-key")

        async def mention_handler(
            _client,
            mention,
            *,
            bot_user_id,
            required_role_name,
            admit_request,
        ):
            observed.append((bot_user_id, mention.event_id, required_role_name))
            assert admit_request(
                DumpRequest(
                    room_id=mention.room_id,
                    command_event_id=mention.event_id,
                    thread_root_event_id=mention.thread_root_event_id,
                )
            )

        async def process_request(_client, request, *, api_key, temp_root):
            assert request.command_event_id == "command"
            assert api_key == "fake-key"
            assert temp_root == tmp_path

        with pytest.raises(BotRuntimeError) as error:
            await run_bot(
                _settings(tmp_path),
                client_factory=client_factory,
                event_stream_factory=event_stream,
                mention_handler=mention_handler,
                request_processor=process_request,
            )

        assert "private response" not in str(error.value)
        assert "fake-key" not in str(error.value)
        assert observed == [
            ("stream", "bot-user-id", ""),
            ("bot-user-id", "command", "thread-exporter"),
        ]
        assert client.closed

    asyncio.run(scenario())


def test_reception_stays_responsive_while_one_dump_is_held_and_shutdown_discards_queue(
    tmp_path,
) -> None:
    async def scenario() -> None:
        owner_thread = get_ident()
        initial_thread_ids = {thread.ident for thread in active_threads()}
        client = _Client()
        worker_started = asyncio.Event()
        ingestion_finished = asyncio.Event()
        allow_stream_end = asyncio.Event()
        processed: list[str] = []
        admission_results: list[bool] = []
        cleaned_active_request = False

        @asynccontextmanager
        async def client_factory(**_kwargs):
            try:
                yield client
            finally:
                client.closed = True

        async def event_stream(_client, *, bot_user_id):
            assert bot_user_id == "bot-user-id"
            for event_id in ("first-command", "second-command", "third-command"):
                yield _mention(event_id)
            await allow_stream_end.wait()

        async def mention_handler(
            _client,
            mention,
            *,
            bot_user_id,
            required_role_name,
            admit_request,
        ):
            assert get_ident() == owner_thread
            assert bot_user_id == "bot-user-id"
            assert required_role_name == "thread-exporter"
            admission_results.append(
                admit_request(
                    DumpRequest(
                        room_id=mention.room_id,
                        command_event_id=mention.event_id,
                        thread_root_event_id=mention.thread_root_event_id,
                    )
                )
            )
            if mention.event_id == "third-command":
                ingestion_finished.set()

        async def process_request(_client, request, **_kwargs):
            nonlocal cleaned_active_request
            assert get_ident() == owner_thread
            processed.append(request.command_event_id)
            try:
                worker_started.set()
                await asyncio.Future()
            finally:
                cleaned_active_request = True

        runtime = asyncio.create_task(
            run_bot(
                _settings(tmp_path),
                client_factory=client_factory,
                event_stream_factory=event_stream,
                mention_handler=mention_handler,
                request_processor=process_request,
            )
        )
        await asyncio.wait_for(worker_started.wait(), timeout=2)
        await asyncio.wait_for(ingestion_finished.wait(), timeout=2)

        assert admission_results == [True, True, False]
        assert processed == ["first-command"]
        assert get_ident() == owner_thread

        runtime.cancel()
        with pytest.raises(asyncio.CancelledError):
            await runtime

        assert cleaned_active_request
        assert processed == ["first-command"]
        assert client.closed
        assert {thread.ident for thread in active_threads()} == initial_thread_ids

    asyncio.run(scenario())


def test_runtime_sanitizes_authentication_failure_and_closes_client(tmp_path) -> None:
    async def scenario() -> None:
        client = _BrokenClient()

        @asynccontextmanager
        async def client_factory(**_kwargs):
            try:
                yield client
            finally:
                client.closed = True

        with pytest.raises(BotRuntimeError) as error:
            await run_bot(_settings(tmp_path), client_factory=client_factory)

        assert str(error.value) == "Could not authenticate the Chatto bot."
        assert "fake-key" not in str(error.value)
        assert client.closed

    asyncio.run(scenario())


def test_installed_entrypoint_is_the_runtime_main() -> None:
    from chatto_threaddump import main
    from chatto_threaddump.runtime import main as runtime_main

    assert main is runtime_main


def test_failed_reply_for_one_mention_does_not_stop_live_reception(
    tmp_path, caplog
) -> None:
    async def scenario() -> None:
        @asynccontextmanager
        async def client_factory(**_kwargs):
            yield _Client()

        async def event_stream(_client, *, bot_user_id):
            yield _mention("first-command")
            yield _mention("second-command")
            raise ChattoStreamError("private stream details")

        observed: list[str] = []

        async def mention_handler(_client, mention, **_kwargs):
            if mention.event_id == "first-command":
                raise RuntimeError("private reply details")
            observed.append(mention.event_id)

        with pytest.raises(BotRuntimeError):
            await run_bot(
                _settings(tmp_path),
                client_factory=client_factory,
                event_stream_factory=event_stream,
                mention_handler=mention_handler,
            )

        assert observed == ["second-command"]
        assert "Could not process a Chatto mention." in caplog.text
        assert "private reply details" not in caplog.text
        assert "private stream details" not in caplog.text

    asyncio.run(scenario())
