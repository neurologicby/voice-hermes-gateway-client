"""Асинхронный WebSocket-транспорт VoiceGateway без привязки к Qt-потоку."""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from enum import Enum
from typing import Any, Protocol, TypeAlias, cast
from urllib.parse import urlparse

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from voice_client.net.protocol import (
    ClientAudioTurn,
    ClientProtocolError,
    SpeechLanguage,
    hello,
    pair_request,
)

JSONFrame = dict[str, Any]
WireFrame: TypeAlias = str | bytes
Callback: TypeAlias = Callable[[Any], None | Awaitable[None]]


class PlaybackSink(Protocol):
    def start(self, message: JSONFrame) -> None: ...

    def push(self, pcm_s16le: bytes) -> None: ...

    def finish(self, message: JSONFrame) -> None: ...

    def interrupt(self) -> None: ...


class WebSocketConnection(Protocol):
    def __aiter__(self) -> AsyncIterator[str | bytes]: ...

    async def send(self, message: WireFrame) -> None: ...

    async def close(self) -> None: ...


Connector: TypeAlias = Callable[..., AsyncIterator[WebSocketConnection]]


class ConnectionState(str, Enum):
    STOPPED = "stopped"
    CONNECTING = "connecting"
    AWAITING_HELLO = "awaiting_hello"
    PAIR_REQUIRED = "pair_required"
    READY = "ready"
    DISCONNECTED = "disconnected"


class ClientConnectionError(RuntimeError):
    """Операция недоступна в текущем состоянии транспорта."""


class OutboundQueueFull(ClientConnectionError):
    """Bounded очередь заполнена: producer обязан применить backpressure."""


class VoiceWSClient:
    """Reconnect-loop, handshake и маршрутизация protocol v1."""

    def __init__(
        self,
        url: str,
        *,
        device_id: str,
        user: str,
        playback: PlaybackSink,
        client_name: str = "voice-client/0.1",
        language: SpeechLanguage = "ru",
        outbound_limit: int = 128,
        connector: Connector | None = None,
        on_event: Callback | None = None,
        on_state: Callback | None = None,
    ) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in {"ws", "wss"} or not parsed.netloc:
            raise ValueError("VoiceGateway URL must use ws:// or wss://")
        if outbound_limit < 1:
            raise ValueError("outbound_limit must be positive")
        self.url = url
        self.device_id = device_id
        self.user = user
        self.client_name = client_name
        self.playback = playback
        self.audio_turn = ClientAudioTurn(language)
        self.state = ConnectionState.STOPPED
        self._hello = hello(device_id, user, client_name)
        self._connector = connector or cast(Connector, connect)
        self._on_event = on_event
        self._on_state = on_state
        self._outbound: asyncio.Queue[tuple[int, WireFrame]] = asyncio.Queue(outbound_limit)
        self._generation = 0
        self._stopping = False
        self._socket: WebSocketConnection | None = None

    async def run(self) -> None:
        """Работает до stop(); reconnect и backoff выполняет websockets."""

        self._stopping = False
        await self._set_state(ConnectionState.CONNECTING)
        connection_stream = self._connector(
            self.url,
            max_size=64 * 1024,
            max_queue=16,
            ping_interval=20,
            ping_timeout=20,
            compression=None,
        )
        try:
            async for socket in connection_stream:
                if self._stopping:
                    break
                self._socket = socket
                self._generation += 1
                self._discard_stale_streams()
                await self._set_state(ConnectionState.AWAITING_HELLO)
                await socket.send(_json(self._hello))
                try:
                    await self._serve(socket, self._generation)
                except ConnectionClosed:
                    pass
                finally:
                    self._socket = None
                    self._discard_stale_streams()
                    if not self._stopping:
                        await self._set_state(ConnectionState.DISCONNECTED)
                        await self._set_state(ConnectionState.CONNECTING)
        finally:
            self._socket = None
            self._discard_stale_streams()
            await self._set_state(ConnectionState.STOPPED)

    async def stop(self) -> None:
        self._stopping = True
        socket = self._socket
        if socket is not None:
            await socket.close()

    def set_language(self, language: SpeechLanguage) -> None:
        self.audio_turn.set_language(language)

    def request_pairing(self, user_name: str) -> None:
        self._enqueue_json(pair_request(self.device_id, user_name), require_ready=False)

    def retry_hello(self) -> None:
        """Повторяет approval-check на том же сокете после подтверждения кода."""

        self._enqueue_json(self._hello, require_ready=False)

    def begin_audio(self, seq: int) -> None:
        self._enqueue_json(self.audio_turn.begin(seq))

    def send_audio(self, pcm_s16le: bytes) -> None:
        self._enqueue(self.audio_turn.chunk(pcm_s16le))

    def end_audio(self) -> None:
        frame = self.audio_turn.request_end()
        if frame is not None:
            self._enqueue_json(frame)

    def send_text(self, text: str) -> None:
        if not text or len(text) > 16_384:
            raise ClientProtocolError("text must contain 1..16384 characters")
        self._enqueue_json({"type": "text", "text": text})

    def interrupt(self) -> None:
        """Синхронно очищает playback до постановки interrupt в wire-очередь."""

        self.playback.interrupt()
        self.audio_turn.interrupt()
        self._enqueue_json({"type": "interrupt"})

    async def _serve(self, socket: WebSocketConnection, generation: int) -> None:
        sender = asyncio.create_task(self._send_loop(socket, generation))
        receiver = asyncio.create_task(self._receive_loop(socket))
        try:
            done, _ = await asyncio.wait(
                {sender, receiver},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in done:
                task.result()
        finally:
            sender.cancel()
            receiver.cancel()
            await asyncio.gather(sender, receiver, return_exceptions=True)

    async def _receive_loop(self, socket: WebSocketConnection) -> None:
        async for message in socket:
            await self._route(message)

    async def _send_loop(self, socket: WebSocketConnection, generation: int) -> None:
        while True:
            frame_generation, frame = await self._outbound.get()
            if frame_generation != generation:
                continue
            await socket.send(frame)

    async def _route(self, wire: str | bytes) -> None:
        if isinstance(wire, bytes):
            self.playback.push(wire)
            return
        try:
            message = json.loads(wire)
        except (json.JSONDecodeError, UnicodeError) as exc:
            raise ClientProtocolError("server control frame is not valid JSON") from exc
        if not isinstance(message, dict) or not isinstance(message.get("type"), str):
            raise ClientProtocolError("server control frame must be a typed object")
        frame = cast(JSONFrame, message)
        frame_type = frame["type"]
        if frame_type == "hello_ok":
            if frame.get("proto") not in {None, 1}:
                raise ClientProtocolError("server negotiated an incompatible protocol")
            await self._set_state(ConnectionState.READY)
        elif frame_type == "pair_required":
            await self._set_state(ConnectionState.PAIR_REQUIRED)
        elif frame_type == "tts_start":
            self.playback.start(frame)
        elif frame_type == "tts_end":
            self.playback.finish(frame)
        elif frame_type == "vad_endpoint":
            seq = frame.get("seq")
            if isinstance(seq, bool) or not isinstance(seq, int):
                raise ClientProtocolError("invalid server VAD sequence")
            end = self.audio_turn.server_vad_endpoint(seq)
            if end is not None:
                self._enqueue_json(end)
        elif frame_type == "final":
            seq = frame.get("seq")
            if isinstance(seq, bool) or not isinstance(seq, int):
                raise ClientProtocolError("invalid final sequence")
            self.audio_turn.accept_final(seq)
        await self._emit(self._on_event, frame)

    def _enqueue_json(self, frame: JSONFrame, *, require_ready: bool = True) -> None:
        self._enqueue(_json(frame), require_ready=require_ready)

    def _enqueue(self, frame: WireFrame, *, require_ready: bool = True) -> None:
        allowed = self.state is ConnectionState.READY
        if not allowed and not (
            not require_ready and self.state is ConnectionState.PAIR_REQUIRED
        ):
            raise ClientConnectionError("VoiceGateway connection is not ready")
        try:
            self._outbound.put_nowait((self._generation, frame))
        except asyncio.QueueFull as exc:
            raise OutboundQueueFull("VoiceGateway outbound queue is full") from exc

    def _discard_stale_streams(self) -> None:
        self.audio_turn.interrupt()
        self.playback.interrupt()
        while True:
            try:
                self._outbound.get_nowait()
            except asyncio.QueueEmpty:
                break

    async def _set_state(self, state: ConnectionState) -> None:
        if state is self.state:
            return
        self.state = state
        await self._emit(self._on_state, state)

    @staticmethod
    async def _emit(callback: Callback | None, value: Any) -> None:
        if callback is None:
            return
        result = callback(value)
        if inspect.isawaitable(result):
            await result


def _json(frame: JSONFrame) -> str:
    return json.dumps(frame, ensure_ascii=False, separators=(",", ":"))


__all__ = [
    "ClientConnectionError",
    "ConnectionState",
    "OutboundQueueFull",
    "VoiceWSClient",
]
