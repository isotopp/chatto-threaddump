from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from chattolib import ChattoClient

from chatto_threaddump.admission import handle_mention
from chatto_threaddump.chatto_events import MentionInput, live_mentions
from chatto_threaddump.config import ConfigurationError, Settings, load_settings
from chatto_threaddump.contracts import DumpRequest
from chatto_threaddump.inbox import RequestInbox
from chatto_threaddump.worker import process_dump_request

_LOGGER = logging.getLogger(__name__)
_SHUTDOWN_SIGNALS = (signal.SIGTERM,)


class BotRuntimeError(Exception):
    """A sanitized failure that should stop the long-running bot."""


ClientFactory = Callable[..., Any]
EventStreamFactory = Callable[..., AsyncIterator[MentionInput]]
MentionHandler = Callable[..., Awaitable[None]]
RequestProcessor = Callable[..., Awaitable[None]]


async def run_bot(
    settings: Settings,
    *,
    client_factory: ClientFactory = ChattoClient,
    event_stream_factory: EventStreamFactory = live_mentions,
    mention_handler: MentionHandler = handle_mention,
    request_processor: RequestProcessor = process_dump_request,
) -> None:
    """Run live reception and one sequential dump consumer on this event loop."""
    try:
        async with client_factory(
            token=settings.api_key,
            base_url=settings.server_url,
        ) as client:
            try:
                profile = await client.me()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - auth errors can contain private details.
                raise BotRuntimeError(
                    "Could not authenticate the Chatto bot."
                ) from None

            bot_user_id = getattr(profile, "id", None)
            if not isinstance(bot_user_id, str) or not bot_user_id:
                raise BotRuntimeError("Chatto did not return the bot user identity.")

            inbox = RequestInbox(settings.queue_length)

            async def process_request(request: DumpRequest) -> None:
                try:
                    await request_processor(
                        client,
                        request,
                        api_key=settings.api_key,
                        temp_root=settings.temp_dir,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 - never leak request details.
                    _LOGGER.warning("Could not complete a thread dump request.")

            async def receive_mentions() -> None:
                try:
                    async for mention in event_stream_factory(
                        client,
                        bot_user_id=bot_user_id,
                    ):
                        try:
                            await mention_handler(
                                client,
                                mention,
                                bot_user_id=bot_user_id,
                                required_role_name=settings.role_name,
                                admit_request=inbox.admit,
                            )
                        except asyncio.CancelledError:
                            raise
                        except Exception:  # noqa: BLE001 - reply failures are per-message.
                            # An individual reply or malformed message must not
                            # take the long-running listener down.
                            _LOGGER.warning("Could not process a Chatto mention.")
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 - event errors may contain payloads.
                    raise BotRuntimeError("Chatto realtime stream failed.") from None
                raise BotRuntimeError("Chatto realtime stream ended unexpectedly.")

            try:
                async with asyncio.TaskGroup() as tasks:
                    tasks.create_task(inbox.run(process_request))
                    tasks.create_task(receive_mentions())
            except ExceptionGroup:
                raise BotRuntimeError("Chatto realtime stream failed.") from None
    except asyncio.CancelledError:
        raise
    except BotRuntimeError:
        raise
    except Exception:  # noqa: BLE001 - client errors may include private data.
        raise BotRuntimeError("Chatto bot runtime failed.") from None


async def _run_with_shutdown(settings: Settings) -> None:
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    installed_signals: list[signal.Signals] = []
    if task is not None:
        for signum in _SHUTDOWN_SIGNALS:
            try:
                loop.add_signal_handler(signum, task.cancel)
            except NotImplementedError, RuntimeError, ValueError:
                continue
            installed_signals.append(signum)
    try:
        await run_bot(settings)
    finally:
        for signum in installed_signals:
            loop.remove_signal_handler(signum)


def main() -> int:
    """Load dotenv configuration and run until shutdown or a stream failure."""
    logging.basicConfig(level=logging.INFO)
    try:
        settings = load_settings()
    except ConfigurationError as error:
        _LOGGER.error("Invalid Chatto bot configuration: %s", error)
        return 1

    try:
        asyncio.run(_run_with_shutdown(settings))
    except KeyboardInterrupt, asyncio.CancelledError:
        return 0
    except BotRuntimeError as error:
        _LOGGER.error("%s", error)
        return 1
    return 1
