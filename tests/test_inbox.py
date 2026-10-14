import asyncio

from chatto_threaddump.contracts import DumpRequest
from chatto_threaddump.inbox import RequestInbox


def _request(event_id: str) -> DumpRequest:
    return DumpRequest(
        room_id="R12345678901234",
        command_event_id=event_id,
        thread_root_event_id="E12345678901234",
    )


def test_inbox_admits_without_waiting_and_consumes_fifo_sequentially() -> None:
    async def scenario() -> None:
        inbox = RequestInbox(capacity=2)
        first_started = asyncio.Event()
        release_first = asyncio.Event()
        second_started = asyncio.Event()
        release_second = asyncio.Event()
        all_finished = asyncio.Event()
        processed: list[str] = []
        active = 0
        maximum_active = 0

        async def process(request: DumpRequest) -> None:
            nonlocal active, maximum_active
            active += 1
            maximum_active = max(maximum_active, active)
            processed.append(request.command_event_id)
            if request.command_event_id == "E00000000000001":
                first_started.set()
                await release_first.wait()
            elif request.command_event_id == "E00000000000002":
                second_started.set()
                await release_second.wait()
            if len(processed) == 4:
                all_finished.set()
            active -= 1

        consumer = asyncio.create_task(inbox.run(process))
        try:
            assert inbox.admit(_request("E00000000000001"))
            await first_started.wait()

            assert inbox.admit(_request("E00000000000002"))
            assert inbox.admit(_request("E00000000000003"))
            assert not inbox.admit(_request("E00000000000004"))

            release_first.set()
            await second_started.wait()
            assert inbox.admit(_request("E00000000000004"))
            release_second.set()
            await all_finished.wait()
        finally:
            consumer.cancel()
            try:
                await consumer
            except asyncio.CancelledError:
                pass

        assert processed == [
            "E00000000000001",
            "E00000000000002",
            "E00000000000003",
            "E00000000000004",
        ]
        assert maximum_active == 1

    asyncio.run(scenario())


def test_new_inbox_starts_with_no_prior_requests() -> None:
    async def scenario() -> None:
        unfinished_inbox = RequestInbox(capacity=1)
        assert unfinished_inbox.admit(_request("E00000000000006"))

        inbox = RequestInbox(capacity=1)
        processed: list[str] = []
        finished = asyncio.Event()

        async def process(request: DumpRequest) -> None:
            processed.append(request.command_event_id)
            finished.set()

        assert inbox.admit(_request("E00000000000005"))
        consumer = asyncio.create_task(inbox.run(process))
        try:
            await finished.wait()
        finally:
            consumer.cancel()
            try:
                await consumer
            except asyncio.CancelledError:
                pass

        assert processed == ["E00000000000005"]

    asyncio.run(scenario())
