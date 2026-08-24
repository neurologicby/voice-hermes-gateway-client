from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from voice_client.net.ws_client import (
    ConnectionState,
    OutboundQueueFull,
    VoiceWSClient,
)

DEVICE_ID = "9f5e5b18-0d07-47da-8ed4-4c3a67dd535e"


class FakePlayback:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def start(self, message: dict[str, Any]) -> None:
        self.calls.append(("start", message))

    def push(self, pcm_s16le: bytes) -> None:
        self.calls.append(("push", pcm_s16le))

    def finish(self, message: dict[str, Any]) -> None:
        self.calls.append(("finish", message))

    def interrupt(self) -> None:
        self.calls.append(("interrupt", None))


class FakeSocket:
    def __init__(self) -> None:
        self.sent: list[str | bytes] = []
        self.incoming: asyncio.Queue[str | bytes | None] = asyncio.Queue()

    def __aiter__(self) -> FakeSocket:
        return self

    async def __anext__(self) -> str | bytes:
        message = await self.incoming.get()
        if message is None:
            raise StopAsyncIteration
        return message

    async def send(self, message: str | bytes) -> None:
        self.sent.append(message)

    async def close(self) -> None:
        self.incoming.put_nowait(None)

    def receive(self, message: dict[str, Any] | bytes) -> None:
        wire = message if isinstance(message, bytes) else json.dumps(message)
        self.incoming.put_nowait(wire)


class FakeConnector:
    def __init__(self, *sockets: FakeSocket) -> None:
        self.sockets = iter(sockets)
        self.options: dict[str, Any] = {}

    def __call__(self, url: str, **kwargs: Any) -> FakeConnector:
        self.options = {"url": url, **kwargs}
        return self

    def __aiter__(self) -> FakeConnector:
        return self

    async def __anext__(self) -> FakeSocket:
        try:
            return next(self.sockets)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


async def _spin_until(predicate: Any) -> None:
    for _ in range(100):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition wasn't reached")


def test_handshake_audio_vad_and_tts_routing() -> None:
    asyncio.run(_handshake_audio_vad_and_tts_routing())


async def _handshake_audio_vad_and_tts_routing() -> None:
    socket = FakeSocket()
    playback = FakePlayback()
    events: list[dict[str, Any]] = []
    client = VoiceWSClient(
        "ws://127.0.0.1:8765/ws",
        device_id=DEVICE_ID,
        user="dmitry",
        playback=playback,
        connector=FakeConnector(socket),
        on_event=events.append,
    )
    task = asyncio.create_task(client.run())
    await _spin_until(lambda: bool(socket.sent))
    assert json.loads(str(socket.sent[0]))["type"] == "hello"

    socket.receive({"type": "hello_ok", "proto": 1, "session": "voice:dmitry"})
    await _spin_until(lambda: client.state is ConnectionState.READY)
    client.set_language("en")
    client.begin_audio(7)
    client.send_audio(b"\x01\x00")
    socket.receive({"type": "vad_endpoint", "seq": 7})
    socket.receive({"type": "final", "seq": 7, "text": "hello"})
    start = {
        "type": "tts_start",
        "stream_id": "tts-7",
        "format": {"sample_rate": 24_000, "channels": 1, "sample_width": 2},
    }
    socket.receive(start)
    socket.receive(b"\x02\x00")
    socket.receive({"type": "tts_end", "stream_id": "tts-7", "interrupted": False})
    await _spin_until(lambda: len(socket.sent) >= 4 and len(playback.calls) >= 4)

    assert json.loads(str(socket.sent[1])) == {"type": "audio_start", "seq": 7, "lang": "en"}
    assert socket.sent[2] == (7).to_bytes(8, "big") + b"\x01\x00"
    assert json.loads(str(socket.sent[3]))["vad"] == "server_silence"
    assert [call[0] for call in playback.calls[-3:]] == ["start", "push", "finish"]
    assert events[-1]["type"] == "tts_end"

    await client.stop()
    await task
    assert client.state is ConnectionState.STOPPED


def test_reconnect_repeats_hello_and_discards_stale_stream() -> None:
    asyncio.run(_reconnect_repeats_hello_and_discards_stale_stream())


async def _reconnect_repeats_hello_and_discards_stale_stream() -> None:
    first = FakeSocket()
    second = FakeSocket()
    playback = FakePlayback()
    client = VoiceWSClient(
        "wss://voice.example/ws",
        device_id=DEVICE_ID,
        user="user",
        playback=playback,
        connector=FakeConnector(first, second),
    )
    task = asyncio.create_task(client.run())
    await _spin_until(lambda: bool(first.sent))
    first.receive({"type": "hello_ok", "proto": 1})
    await _spin_until(lambda: client.state is ConnectionState.READY)
    client.begin_audio(1)
    await first.close()
    await _spin_until(lambda: bool(second.sent))
    assert json.loads(str(second.sent[0]))["type"] == "hello"
    assert client.audio_turn.active_seq is None
    assert [name for name, _ in playback.calls].count("interrupt") >= 2
    await client.stop()
    await task


def test_outbound_queue_is_bounded() -> None:
    client = VoiceWSClient(
        "ws://localhost/ws",
        device_id=DEVICE_ID,
        user="user",
        playback=FakePlayback(),
        outbound_limit=1,
    )
    client.state = ConnectionState.READY
    client.begin_audio(1)
    with pytest.raises(OutboundQueueFull):
        client.send_audio(b"\x00\x00")


def test_barge_in_clears_playback_before_enqueue() -> None:
    playback = FakePlayback()
    client = VoiceWSClient(
        "ws://localhost/ws",
        device_id=DEVICE_ID,
        user="user",
        playback=playback,
    )
    client.state = ConnectionState.READY
    client.interrupt()
    assert playback.calls == [("interrupt", None)]


def test_pairing_and_hello_retry_are_allowed_before_ready() -> None:
    client = VoiceWSClient(
        "ws://localhost/ws",
        device_id=DEVICE_ID,
        user="user",
        playback=FakePlayback(),
    )
    client.state = ConnectionState.PAIR_REQUIRED
    client.request_pairing("Иванов Иван")
    client.retry_hello()
    assert client._outbound.qsize() == 2


def test_ping_and_test_are_allowed_while_awaiting_hello() -> None:
    client = VoiceWSClient(
        "ws://localhost/ws",
        device_id=DEVICE_ID,
        user="user",
        playback=FakePlayback(),
    )
    client.state = ConnectionState.AWAITING_HELLO
    client.send_ping()
    client.send_test()
    assert client._outbound.qsize() == 2
