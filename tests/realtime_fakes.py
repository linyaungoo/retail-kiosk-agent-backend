"""Test doubles for the Realtime voice mode: a simulated OpenAI HTTP API (real SDK) and
a fake sideband connection."""

import asyncio
import json
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import httpx2
from openai import AsyncOpenAI
from websockets.exceptions import ConnectionClosedOK

SDP_OFFER = "v=0\r\no=- 1 2 IN IP4 127.0.0.1\r\ns=-\r\n"
SDP_ANSWER = "v=0\r\no=- answer\r\ns=-\r\n"


class FakeOpenAI:
    """Records calls.create / calls.hangup made through the real SDK."""

    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.hung_up: list[str] = []
        self.status = 201
        self._counter = 0

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        if path == "/v1/realtime/calls":
            if self.status != 201:
                return httpx2.Response(self.status, json={"error": {"message": "nope"}})
            self._counter += 1
            body = request.read().decode()
            session = json.loads(
                body.split('name="session"')[1].split("\r\n\r\n", 1)[1].split("\r\n--")[0]
            )
            self.created.append(session)
            return httpx2.Response(
                201,
                text=SDP_ANSWER,
                headers={"Location": f"/v1/realtime/calls/rtc_test{self._counter}"},
            )
        if path.endswith("/hangup"):
            self.hung_up.append(path.split("/")[-2])
            return httpx2.Response(200, json={})
        return httpx2.Response(404, json={"error": {"message": path}})

    def client(self) -> AsyncOpenAI:
        return AsyncOpenAI(
            api_key="sk-test",
            max_retries=0,
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(self.handler)),
        )


class FakeConnection:
    """Sideband connection fed by the test; records what the backend sends."""

    def __init__(self) -> None:
        self.events: asyncio.Queue[Any] = asyncio.Queue()
        self.sent: list[dict[str, Any]] = []
        self.closed = False
        self.sent_signal = asyncio.Event()

    def push(self, kind: str, **fields: Any) -> None:
        self.events.put_nowait(ns(type=kind, **fields))

    def disconnect(self) -> None:
        self.events.put_nowait(_CLOSE)

    async def recv(self) -> Any:
        event = await self.events.get()
        if event is _CLOSE:
            raise ConnectionClosedOK(None, None)
        return event

    async def send(self, event: Any) -> None:
        self.sent.append(event)
        self.sent_signal.set()

    async def close(self) -> None:
        self.closed = True


_CLOSE = object()


def ns(**fields: Any) -> SimpleNamespace:
    return SimpleNamespace(**fields)


def function_call_done(
    name: str, arguments: dict[str, Any], call_id: str = "fc_1"
) -> SimpleNamespace:
    return ns(
        type="response.done",
        response=ns(
            status="completed",
            output=[
                ns(
                    type="function_call",
                    name=name,
                    arguments=json.dumps(arguments),
                    call_id=call_id,
                )
            ],
            usage=ns(input_tokens=100, output_tokens=5),
        ),
    )


async def wait_for(condition: Callable[[], bool], limit: float = 2.0) -> None:
    async with asyncio.timeout(limit):
        while not condition():  # noqa: ASYNC110 - polls arbitrary state in tests
            await asyncio.sleep(0.01)
