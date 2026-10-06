"""ASGI middleware."""

import json

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.errors import ErrorCode


class _BodyTooLarge(Exception):
    pass


class BodySizeLimitMiddleware:
    """Reject request bodies over `max_bytes` before they are buffered.

    Checks Content-Length up front, and counts bytes as they arrive for chunked
    uploads that don't declare a length.

    For a moderately oversized upload (e.g. a too-long recording) the body is read
    and discarded before answering, so clients actually receive the 413 instead of
    a "connection reset". Anything beyond DRAIN_FACTOR x the limit is cut off.
    """

    DRAIN_FACTOR = 4

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes
        error = {"code": ErrorCode.REQUEST_TOO_LARGE, "message": "Request body is too large."}
        self._body = json.dumps({"success": False, "error": error}).encode()

    async def _send_413(self, send: Send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(self._body)).encode()),
                    (b"connection", b"close"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": self._body})

    @staticmethod
    async def _drain(receive: Receive) -> None:
        while True:
            message = await receive()
            if message["type"] != "http.request" or not message.get("more_body", False):
                return

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > self.max_bytes:
            if int(declared) <= self.max_bytes * self.DRAIN_FACTOR:
                await self._drain(receive)
            await self._send_413(send)
            return

        received = 0
        too_large = False
        replaced = False

        async def limited_receive() -> Message:
            nonlocal received, too_large
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    too_large = True
                    raise _BodyTooLarge
            return message

        async def guarded_send(message: Message) -> None:
            # FastAPI turns errors raised while reading the body into a generic 400;
            # swap whatever the app answers for our 413.
            nonlocal replaced
            if not too_large:
                await send(message)
            elif not replaced:
                replaced = True
                await self._send_413(send)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except _BodyTooLarge:
            if not replaced:
                await self._send_413(send)
