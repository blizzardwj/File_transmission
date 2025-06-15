"""layered_transfer.py

SOLID-compliant layered implementation of message / file transfer over a generic
transport.  This file isolates the responsibilities that were previously mixed
in `socket_transfer_subject.py`.

Layers
------
1. Transport          – raw bytes I/O (TCP / SSH / TLS / Mock).
2. HeaderCodec        – frame packing / unpacking (10-byte length + header).
3. HandshakeManager   – control-flow protocol for file transfer.
4. ChunkTransfer      – unified large payload streaming with buffer & progress.
5. FileTransferService – high-level public API for send / receive.
6. MessageService     – simple text messages.

Cross-cutting concerns (logging, progress events, buffer management) are injected 
to avoid tight coupling. All layers depend on abstractions, enabling easy 
replacement or extension.

Author: Cascade refactor
"""
from __future__ import annotations

from re import S
import socket
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol, Union, Callable, BinaryIO

from core.utils import build_logger
from core.progress_observer import ProgressSubject
from core.progress_events import (
    TaskStartedEvent,
    ProgressAdvancedEvent,
    TaskFinishedEvent,
    TaskErrorEvent,
    generate_task_id,
)
from core.network_utils import BufferManager
import transfer

logger = build_logger(__name__)

# ---------------------------------------------------------------------------
# 1. Transport Layer
# ---------------------------------------------------------------------------
class Transport(Protocol):
    """Minimal interface for a full-duplex byte stream."""

    @abstractmethod
    def read_exact(self, n: int) -> bytes:
        """Block until exactly *n* bytes are read or raise IOError."""

    @abstractmethod
    def write_all(self, data: bytes) -> None:
        """Write the entire *data* buffer or raise IOError."""

    @abstractmethod
    def close(self) -> None:
        """Close the underlying stream (optional for mocks)."""

    @abstractmethod
    def update_buffer_size(self, new_size: int, role: str) -> None:
        """Update the transport's buffer size for the given role if significantly changed."""

class SocketTransport(Transport):
    """`Transport` implementation backed by a `socket.socket`."""

    def __init__(self, 
        sock: socket.socket, 
        timeout: float = 30.0,
        buffer_size: int = 64 * 1024
    ):
        self._sock = sock
        self._sock.settimeout(timeout)
        
        # Initialize role-specific buffer sizes
        try:
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, buffer_size)
            self._current_snd_buffer_size = buffer_size
        except socket.error as e:
            logger.warning(f"Failed to set initial SNDBUF to {buffer_size}: {e}")
            try:
                self._current_snd_buffer_size = self._sock.getsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF)
            except socket.error:
                self._current_snd_buffer_size = buffer_size

        try:
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, buffer_size)
            self._current_rcv_buffer_size = buffer_size
        except socket.error as e:
            logger.warning(f"Failed to set initial RCVBUF to {buffer_size}: {e}")
            try:
                self._current_rcv_buffer_size = self._sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)
            except socket.error:
                self._current_rcv_buffer_size = buffer_size

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

    # ------------------------------------------------------------------
    # Buffer management
    # ------------------------------------------------------------------
    def set_buffer_size(self, size: int, role: str) -> None:
        """Set the socket buffer size for the specified role, if different from current."""
        if size <= 0:
            raise ValueError("Buffer size must be positive")

        if role == "sender":
            if size != self._current_snd_buffer_size:
                self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, size)
                self._current_snd_buffer_size = size
                logger.debug(f"Socket SNDBUF size updated to {size} bytes")
        elif role == "receiver":
            if size != self._current_rcv_buffer_size:
                self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, size)
                self._current_rcv_buffer_size = size
                logger.debug(f"Socket RCVBUF size updated to {size} bytes")
        else:
            raise ValueError("Role must be 'sender' or 'receiver'")

    def update_buffer_size(self, new_size: int, role: str) -> None:
        """Update socket buffer size for the role if significantly changed."""
        if role == "sender":
            current_val = self._current_snd_buffer_size
        elif role == "receiver":
            current_val = self._current_rcv_buffer_size
        else:
            raise ValueError("Role must be 'sender' or 'receiver'")

        if abs(new_size - current_val) > current_val * 0.1:
            try:
                self.set_buffer_size(new_size, role)
                logger.info(f"Socket {role} buffer size changed to {new_size} bytes")
            except Exception as e:
                logger.error(f"Failed to update socket {role} buffer size: {e}")


# ---------------------------------------------------------------------------
# 2. Framing Layer – HeaderCodec
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class Frame:
    data_type: str  # e.g. "MSG" or "FILE"
    payload: bytes

class HeaderCodec:
    """Encode/decode frames using the 8-byte length + header scheme.
    Protocol format:
    [8-byte length][header][payload]
    where header = "{data_type}|{payload_length}"
    and length = len(header)
    Example:
    [0000000e][MSG|0000000005][Hello]
    The 8-byte length prefix is ASCII-encoded decimal, zero-padded.
    """

    BYTES_FOR_LEN = 8
    HEADER_DELIMITER = "|"

    def encode_frame(self, data_type: str, payload: bytes) -> bytes:
        header = f"{data_type}{self.HEADER_DELIMITER}{len(payload)}".encode('utf-8')
        header_len = f"{len(header):0{self.BYTES_FOR_LEN}d}".encode('utf-8')
        return header_len + header + payload

    def read_frame(self, transport: Transport) -> Frame:
        # 8-byte header length prefix
        header_len = int(transport.read_exact(self.BYTES_FOR_LEN).decode('utf-8'))
        header = transport.read_exact(header_len).decode('utf-8')
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
    SUCCESS = "SUCCESS"
    READY = "READY"
    FAIL = "FAIL"
    METADATA_DELIMITER = "|"

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
        self._send_message(f"{file_name}{self.METADATA_DELIMITER}{file_size}")

    def recv_metadata(self) -> tuple[str, int]:
        meta = self._recv_message()
        name, size_str = meta.split(self.METADATA_DELIMITER)
        return name, int(size_str)

    def send_ready(self) -> None:
        self._send_message(self.READY)

    def await_ready(self) -> None:
        msg = self._recv_message()
        if msg != self.READY:
            raise IOError(f"Expected READY, got {msg}")

    def finalize(self, success: bool) -> None:
        self._send_message(self.SUCCESS if success else self.FAIL)

    def await_final(self) -> None:
        msg = self._recv_message()
        if msg != "SUCCESS":
            raise IOError(f"Transfer not successful: {msg}")

# ---------------------------------------------------------------------------
# 4. Unified Chunk Transfer Layer
# ---------------------------------------------------------------------------
class ChunkTransfer:
    """Unified class for sending and receiving file chunks with consistent task tracking."""

    # Adaptive buffer adjustment constants
    BUFFER_ADJUSTMENT_INTERVAL = 10  # Adjust buffer every N chunks
    def __init__(
        self,
        transport: Transport,
        codec: HeaderCodec,
        buffer_size: int = 64 * 1024,
        buffer_manager: Optional[BufferManager] = None,
    ) -> None:
        self._transport = transport
        self._codec = codec
        self._buffer_size = buffer_size
        self._buffer_manager = buffer_manager
        # Task IDs as class attributes for consistent tracking
        self._send_task_id: Optional[str] = None
        self._receive_task_id: Optional[str] = None

    def set_send_task_id(self, task_id: str) -> None:
        """Set the task ID for sending operations."""
        self._send_task_id = task_id

    def set_receive_task_id(self, task_id: str) -> None:
        """Set the task ID for receiving operations."""
        self._receive_task_id = task_id

    def buffer_size_update_handler(self, chunk_size: int, chunk_time: float, role: str) -> None:
        """Handle buffer size updates when necessary."""
        if self._buffer_manager:
            self._buffer_size = self._buffer_manager.adaptive_adjust(chunk_size, chunk_time)
        self._transport.update_buffer_size(self._buffer_size, role)

    def send(self, 
        file_obj: BinaryIO, 
        progress_subject: Optional[ProgressSubject] = None
    ) -> None:
        """Send file chunks using the pre-set task ID."""
        file_obj.seek(0, 2)
        total_size = file_obj.tell()
        file_obj.seek(0)
        
        # Use pre-set task_id or generate one if not set
        task_id = self._send_task_id or generate_task_id()
        
        if progress_subject:
            progress_subject.notify_observers(TaskStartedEvent(
                task_id=task_id, 
                description=f"Sending {file_obj.name}",
                total=total_size))
        
        try:
            chunk_count = 0
            while True:
                transfer_start = time.time()

                # use current buffer size 
                chunk = file_obj.read(self._buffer_size)
                if not chunk:
                    break
                frame = Frame("FILE", chunk)
                self._codec.write_frame(self._transport, frame)

                transfer_end = time.time()
                transfer_time = transfer_end - transfer_start
                chunk_count += 1

                if chunk_count % self.BUFFER_ADJUSTMENT_INTERVAL == 0 and transfer_time > 0:
                    # Adjust buffer size and set it to self._buffer_size
                    self.buffer_size_update_handler(len(chunk), transfer_time, "sender")
                
                if progress_subject:
                    progress_subject.notify_observers(ProgressAdvancedEvent(task_id, advance=len(chunk)))
            
            if progress_subject:
                progress_subject.notify_observers(TaskFinishedEvent(
                    task_id=task_id,
                    description=f"Sending {file_obj.name} [green]✓  complete",
                    success=True
                ))
        except Exception as exc:
            if progress_subject:
                progress_subject.notify_observers(TaskErrorEvent(task_id, f"Send chunk error: {str(exc)}"))
            raise

    def receive(self, 
        file_obj: BinaryIO, 
        expected_size: int, 
        progress_subject: Optional[ProgressSubject] = None
    ) -> None:
        """Receive file chunks using the pre-set task ID."""
        received = 0
        
        # Use pre-set task_id or generate one if not set
        task_id = self._receive_task_id or generate_task_id()
        
        if progress_subject:
            progress_subject.notify_observers(TaskStartedEvent(
                task_id=task_id, 
                description=f"Receiving {file_obj.name}",
                total=expected_size))
        
        try:
            chunk_count = 0
            while received < expected_size:
                receive_start = time.time()
                
                frame = self._codec.read_frame(self._transport)
                if frame.data_type != "FILE":
                    raise IOError(f"Unexpected frame type {frame.data_type}")
                
                file_obj.write(frame.payload)
                received += len(frame.payload)
                chunk_count += 1
                
                receive_end = time.time()
                receive_time = receive_end - receive_start
                
                # Optional buffer size adjustment for receiver side
                if chunk_count % self.BUFFER_ADJUSTMENT_INTERVAL == 0 and receive_time > 0:
                    self.buffer_size_update_handler(len(frame.payload), receive_time, "receiver")
                
                if progress_subject:
                    progress_subject.notify_observers(ProgressAdvancedEvent(task_id, advance=len(frame.payload)))
            
            if progress_subject:
                progress_subject.notify_observers(TaskFinishedEvent(
                    task_id=task_id,
                    description=f"Receiving {file_obj.name} [green]✓ complete",
                    success=True
                ))
        except Exception as exc:
            if progress_subject:
                progress_subject.notify_observers(TaskErrorEvent(task_id, f"Receive chunk error: {str(exc)}"))
            raise

# ---------------------------------------------------------------------------
# 5. High-level File Transfer Service
# ---------------------------------------------------------------------------
class FileTransferService:
    """Facade that sends or receives a file using lower layers with consistent task tracking."""

    def __init__(
        self,
        transport: Transport,
        buffer_size: int = 64 * 1024,
        progress_subject: Optional[ProgressSubject] = None,
        buffer_manager: Optional[BufferManager] = None
    ) -> None:
        self._codec = HeaderCodec()
        self._transport = transport
        self._handshake = HandshakeManager(transport, self._codec)
        self._chunk_transfer = ChunkTransfer(transport, self._codec, buffer_size)
        self._progress_subject = progress_subject
        self._buffer_manager = buffer_manager

    @classmethod
    def create_with_network_optimization(
        cls,
        transport: Transport,
        progress_subject: Optional[ProgressSubject] = None,
    ) -> FileTransferService:
        """Factory method to create a service with network optimization."""
        from core.network_utils import BufferManager, create_optimized_buffer_manager
        buffer_manager = BufferManager()
        return cls(
            transport=transport,
            progress_subject=progress_subject,
            buffer_manager=buffer_manager
    )

    # --------------- Sending ---------------------------------------------
    def send_file(self, local_path: Union[str, Path]) -> None:
        path = Path(local_path).expanduser()
        if not path.is_file():
            raise FileNotFoundError(path)
        file_size = path.stat().st_size
        
        # Generate consistent task ID for the entire send operation
        task_id = generate_task_id()
        self._chunk_transfer.set_send_task_id(task_id)
        
        logger.info("Starting to send %s (%d bytes)", path.name, file_size)
        self._handshake.send_metadata(path.name, file_size)
        self._handshake.await_ready()
        
        with path.open("rb") as f:
            try:
                self._chunk_transfer.send(f, self._progress_subject)
                self._handshake.finalize(True)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Error during chunk send")
                self._handshake.finalize(False)
                if self._progress_subject:
                    self._progress_subject.notify_observers(TaskErrorEvent(task_id, str(exc)))
                raise
        
        self._handshake.await_final()
        logger.info("File %s sent successfully", path.name)

    # --------------- Receiving ------------------------------------------
    def receive_file(self, output_dir: Union[str, Path] = ".") -> Path:
        out_dir = Path(output_dir).expanduser()
        out_dir.mkdir(parents=True, exist_ok=True)
        name, size = self._handshake.recv_metadata()
        
        # Generate consistent task ID for the entire receive operation
        task_id = generate_task_id()
        self._chunk_transfer.set_receive_task_id(task_id)
        
        logger.info("Preparing to receive %s (%d bytes)", name, size)
        self._handshake.send_ready()
        output_path = out_dir / f"received_{name}"
        
        with output_path.open("wb") as f:
            try:
                self._chunk_transfer.receive(f, size, self._progress_subject)
                self._handshake.finalize(True)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Error during chunk reception")
                self._handshake.finalize(False)
                if self._progress_subject:
                    self._progress_subject.notify_observers(TaskErrorEvent(task_id, str(exc)))
                raise
        
        logger.info("File received successfully: %s", output_path)
        return output_path

# ---------------------------------------------------------------------------
# 6. Simple text messages
# ---------------------------------------------------------------------------
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
    "ChunkTransfer",
    "FileTransferService",
    "MessageService",
]
