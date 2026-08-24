"""Сетевой слой Windows-клиента VoiceGateway."""

from .ws_client import (
    ClientConnectionError,
    ConnectionState,
    OutboundQueueFull,
    VoiceWSClient,
)

__all__ = [
    "ClientConnectionError",
    "ConnectionState",
    "OutboundQueueFull",
    "VoiceWSClient",
]
