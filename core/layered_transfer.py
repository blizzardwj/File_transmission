"""layered_transfer.py

SOLID-compliant layered implementation of message / file transfer over a generic
transport.  This file isolates the responsibilities that were previously mixed
in `socket_transfer_subject.py`.

Layers
------
1. Transport          – raw bytes I/O (TCP / SSH / TLS / Mock).
2. HeaderCodec        – frame packing / unpacking (10-byte length + header).
3. HandshakeManager   – control-flow protocol for file transfer.
4. ChunkSender/Receiver – large payload streaming with buffer & progress.
5. FileTransferService – high-level public API for send / receive.
6. MessageService     – simple text messages.

Cross-cutting concerns (logging, progress events) are injected to avoid tight
coupling.  All layers depend on abstractions, enabling easy replacement or
extension.

Author: Cascade refactor
"""
from __future__ import annotations

import socket
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol, Union

from core.utils import build_logger
from core.progress_observer import ProgressSubject
from core.progress_events import (
    TaskStartedEvent,
    ProgressAdvancedEvent,
    TaskFinishedEvent,
    TaskErrorEvent,
    generate_task_id,
)

logger = build_logger(__name__)

# ---------------------------------------------------------------------------
# 1. Transport Layer
# ---------------------------------------------------------------------------
class Transport(Protocol):
    """Minimal interface for a full-duplex byte stream."""

    def read_exact(self, n: int) -> bytes:
        """Block until exactly *n* bytes are read or raise IOError."""

    def write_all(self, data: bytes) -> None:
        """Write the entire *data* buffer or raise IOError."""

    def close(self) -> None:
        """Close the underlying stream (optional for mocks)."""


class SocketTransport(Transport):
    """`Transport` implementation backed by a `socket.socket`."""

    def __init__(self, sock: socket.socket, timeout: float = 30.0):
        self._sock = sock
        self._sock.settimeout(timeout)

    # ------------------------------------------------------------------
    # Transport interface implementation
    # ------------------------------------------------------------------
    def read_exact(self, n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            chunk = self._sock.recv(n - len(buf))
            if not chunk:
                raise IOError("Socket closed while reading")
            buf.extend(chunk)
        return bytes(buf)

    def write_all(self, data: bytes) -> None:
        total_sent = 0
        while total_sent < len(data):
            sent = self._sock.send(data[total_sent:])
            if sent == 0:
                raise IOError("Socket closed while writing")
            total_sent += sent

    def close(self) -> None:
        try:
            self._sock.close()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# 2. Framing Layer – HeaderCodec
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class Frame:
    data_type: str  # e.g. "MSG" or "FILE"
    payload: bytes


class HeaderCodec:
    """Encode/decode frames using the 10-byte length + header scheme."""

    HEADER_DELIMITER = "|"

    def encode_frame(self, data_type: str, payload: bytes) -> bytes:
        header = f"{data_type}{self.HEADER_DELIMITER}{len(payload)}".encode()
        header_len = f"{len(header):010d}".encode()
        return header_len + header + payload

    def read_frame(self, transport: Transport) -> Frame:
        # 10-byte header length prefix
        header_len = int(transport.read_exact(10).decode())
        header = transport.read_exact(header_len).decode()
        data_type, size_str = header.split(self.HEADER_DELIMITER)
        size = int(size_str)
        payload = transport.read_exact(size)
        return Frame(data_type=data_type, payload=payload)

    def write_frame(self, transport: Transport, frame: Frame) -> None:
        data = self.encode_frame(frame.data_type, frame.payload)
        transport.write_all(data)


# ---------------------------------------------------------------------------
# 3. Handshake Layer
# ---------------------------------------------------------------------------
class HandshakeManager:
    """Control messages for coordinating file transfer."""

    MSG_TYPE = "MSG"

    def __init__(self, transport: Transport, codec: HeaderCodec):
        self._transport = transport
        self._codec = codec

    # ----- Message helpers -------------------------------------------------
    def _send_message(self, message: str) -> None:
        frame = Frame(self.MSG_TYPE, message.encode())
        self._codec.write_frame(self._transport, frame)

    def _recv_message(self) -> str:
        frame = self._codec.read_frame(self._transport)
        if frame.data_type != self.MSG_TYPE:
            raise IOError(f"Expected MSG frame, got {frame.data_type}")
        return frame.payload.decode()

    # ----- Public protocol -------------------------------------------------
    def send_metadata(self, file_name: str, file_size: int) -> None:
        self._send_message(f"{file_name}|{file_size}")

    def recv_metadata(self) -> tuple[str, int]:
        meta = self._recv_message()
        name, size_str = meta.split("|")
        return name, int(size_str)

    def send_ready(self) -> None:
        self._send_message("READY")

    def await_ready(self) -> None:
        msg = self._recv_message()
        if msg != "READY":
            raise IOError(f"Expected READY, got {msg}")

    def finalize(self, success: bool) -> None:
        self._send_message("SUCCESS" if success else "FAIL")

    def await_final(self) -> None:
        msg = self._recv_message()
        if msg != "SUCCESS":
            raise IOError(f"Transfer not successful: {msg}")


# ---------------------------------------------------------------------------
# 4. Chunk Transfer Layer
# ---------------------------------------------------------------------------
class ChunkSender:
    def __init__(
        self,
        transport: Transport,
        codec: HeaderCodec,
        buffer_size: int = 64 * 1024,
    ) -> None:
        self._transport = transport
        self._codec = codec
        self._buffer_size = buffer_size

    def send(self, file_obj, observer: Optional[ProgressSubject] = None) -> None:
        file_obj.seek(0, 2)
        total_size = file_obj.tell()
        file_obj.seek(0)
        sent = 0
        task_id = generate_task_id()
        if observer:
            observer.notify_observers(TaskStartedEvent(
                task_id=task_id, 
                description=f"Sending {file_obj.name}",
                total=total_size))
        while True:
            chunk = file_obj.read(self._buffer_size)
            if not chunk:
                break
            frame = Frame("FILE", chunk)
            self._codec.write_frame(self._transport, frame)
            sent += len(chunk)
            if observer:
                observer.notify_observers(ProgressAdvancedEvent(task_id, sent))
        if observer:
            observer.notify_observers(TaskFinishedEvent(task_id))


class ChunkReceiver:
    def __init__(
        self,
        transport: Transport,
        codec: HeaderCodec,
        buffer_size: int = 64 * 1024,
    ) -> None:
        self._transport = transport
        self._codec = codec
        self._buffer_size = buffer_size

    def receive(self, file_obj, expected_size: int, observer: Optional[ProgressSubject] = None) -> None:
        received = 0
        task_id = generate_task_id()
        if observer:
            observer.notify_observers(TaskStartedEvent(
                task_id=task_id, 
                description=f"Receiving {file_obj.name}",
                total=expected_size))
        while received < expected_size:
            frame = self._codec.read_frame(self._transport)
            if frame.data_type != "FILE":
                raise IOError(f"Unexpected frame type {frame.data_type}")
            file_obj.write(frame.payload)
            received += len(frame.payload)
            if observer:
                observer.notify_observers(ProgressAdvancedEvent(task_id, received))
        if observer:
            observer.notify_observers(TaskFinishedEvent(task_id))


# ---------------------------------------------------------------------------
# 5. High-level File Transfer Service
# ---------------------------------------------------------------------------
class FileTransferService:
    """Facade that sends or receives a file using lower layers."""

    def __init__(
        self,
        transport: Transport,
        buffer_size: int = 64 * 1024,
        observer: Optional[ProgressSubject] = None,
    ) -> None:
        self._codec = HeaderCodec()
        self._transport = transport
        self._handshake = HandshakeManager(transport, self._codec)
        self._sender = ChunkSender(transport, self._codec, buffer_size)
        self._receiver = ChunkReceiver(transport, self._codec, buffer_size)
        self._observer = observer

    # --------------- Sending ---------------------------------------------
    def send_file(self, local_path: Union[str, Path]) -> None:
        path = Path(local_path).expanduser()
        if not path.is_file():
            raise FileNotFoundError(path)
        file_size = path.stat().st_size
        logger.info("Starting to send %s (%d bytes)", path.name, file_size)
        self._handshake.send_metadata(path.name, file_size)
        self._handshake.await_ready()
        with path.open("rb") as f:
            try:
                self._sender.send(f, self._observer)
                self._handshake.finalize(True)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Error during chunk send")
                self._handshake.finalize(False)
                if self._observer:
                    self._observer.notify_observers(TaskErrorEvent(generate_task_id(), str(exc)))
                raise
        self._handshake.await_final()
        logger.info("File %s sent successfully", path.name)

    # --------------- Receiving ------------------------------------------
    def receive_file(self, output_dir: Union[str, Path] = ".") -> Path:
        out_dir = Path(output_dir).expanduser()
        out_dir.mkdir(parents=True, exist_ok=True)
        name, size = self._handshake.recv_metadata()
        logger.info("Preparing to receive %s (%d bytes)", name, size)
        self._handshake.send_ready()
        output_path = out_dir / f"received_{name}"
        with output_path.open("wb") as f:
            try:
                self._receiver.receive(f, size, self._observer)
                self._handshake.finalize(True)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Error during chunk reception")
                self._handshake.finalize(False)
                if self._observer:
                    self._observer.notify_observers(TaskErrorEvent(generate_task_id(), str(exc)))
                raise
        logger.info("File received successfully: %s", output_path)
        return output_path


# ---------------------------------------------------------------------------
# 6. Simple text messages ---------------------------------------------------
class MessageService:
    def __init__(self, transport: Transport):
        self._codec = HeaderCodec()
        self._transport = transport

    def send(self, message: str) -> None:
        frame = Frame("MSG", message.encode())
        self._codec.write_frame(self._transport, frame)

    def recv(self) -> str:
        frame = self._codec.read_frame(self._transport)
        if frame.data_type != "MSG":
            raise IOError(f"Expected MSG frame, got {frame.data_type}")
        return frame.payload.decode()


__all__ = [
    "Transport",
    "SocketTransport",
    "HeaderCodec",
    "HandshakeManager",
    "ChunkSender",
    "ChunkReceiver",
    "FileTransferService",
    "MessageService",
]
