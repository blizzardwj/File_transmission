#!/usr/bin/env python3
"""
Protocol Handler Module

This module defines the ProtocolHandler class, responsible for encoding and
decoding data according to the custom data transfer protocol.
"""
from typing import Tuple, Union, Optional, Protocol as TypingProtocol, runtime_checkable
from core.utils import build_logger

logger = build_logger(__name__)

@runtime_checkable
class ReadableStream(TypingProtocol):
    """
    A protocol for a stream that can be read.
    Matches objects like socket.makefile('rb') or io.BytesIO.
    The `__n` parameter is position-only to match common stream interfaces.
    """
    def read(self, __n: int = -1, /) -> bytes: ...
    # We might also need `closed: bool` or `close()` if the handler manages lifecycle.
    # For now, only `read` is strictly required by the decode methods.

class ProtocolHandler:
    """
    Handles the encoding and decoding of data based on a specific protocol.
    """
    MSG_TYPE = "MSG"
    FILE_TYPE = "FILE"
    HEADER_DELIMITER = "|"
    FIXED_HEADER_LENGTH_SIZE = 10  # Size of the part that tells how long the main header is

    def encode_data(self, data_type: str, payload: Union[str, bytes]) -> bytes:
        """
        Encodes data with the protocol header.

        Args:
            data_type: Type of data (MSG or FILE).
            payload: Data to send (string for messages, bytes for files).

        Returns:
            The fully framed message as bytes, ready to be sent.
        """
        if isinstance(payload, str):
            payload_bytes = payload.encode('utf-8')
        else:
            payload_bytes = payload

        payload_size = len(payload_bytes)
        main_header_content = f"{data_type}{self.HEADER_DELIMITER}{payload_size}"
        main_header_bytes = main_header_content.encode('utf-8')

        # Fixed-size header length (10 bytes, zero-padded)
        fixed_header_len_bytes = f"{len(main_header_bytes):0{self.FIXED_HEADER_LENGTH_SIZE}d}".encode('utf-8')

        return fixed_header_len_bytes + main_header_bytes + payload_bytes

    def decode_header_from_stream(self, stream_reader: ReadableStream) -> Tuple[Optional[str], Optional[int], int]:
        """
        Decodes the protocol header from a byte stream.

        Args:
            stream_reader: A stream object with a read(num_bytes) method.

        Returns:
            Tuple of (data_type, payload_size, total_header_bytes_read).
            Returns (None, None, bytes_read) if an error occurs or stream ends prematurely.
        """
        bytes_read_for_len = 0
        header_len_bytes = None # Initialize to ensure it's bound
        bytes_read_for_main_header = 0 # Initialize to ensure it's bound
        try:
            # 1. Read the fixed-size part that tells us the length of the main header
            header_len_bytes = stream_reader.read(self.FIXED_HEADER_LENGTH_SIZE)
            bytes_read_for_len = len(header_len_bytes)

            if not header_len_bytes or bytes_read_for_len < self.FIXED_HEADER_LENGTH_SIZE:
                logger.debug(f"Stream ended or insufficient data while reading fixed header length. Read {bytes_read_for_len} bytes.")
                return None, None, bytes_read_for_len
            
            main_header_len = int(header_len_bytes.decode('utf-8'))
            # bytes_read_for_main_header is already initialized above

            # 2. Read the main header
            main_header_bytes = stream_reader.read(main_header_len)
            bytes_read_for_main_header = len(main_header_bytes)

            if not main_header_bytes or bytes_read_for_main_header < main_header_len:
                logger.debug(f"Stream ended or insufficient data while reading main header. Expected {main_header_len}, got {bytes_read_for_main_header} bytes.")
                return None, None, bytes_read_for_len + bytes_read_for_main_header
            
            main_header_content = main_header_bytes.decode('utf-8')
            data_type, size_str = main_header_content.split(self.HEADER_DELIMITER, 1)
            payload_size = int(size_str)
            
            total_header_bytes_read = self.FIXED_HEADER_LENGTH_SIZE + main_header_len
            return data_type, payload_size, total_header_bytes_read

        except ValueError as e:
            logger.error(f"ValueError during header decoding: {e}. Fixed header bytes: {header_len_bytes if 'header_len_bytes' in locals() else 'N/A'}")
            return None, None, bytes_read_for_len + (bytes_read_for_main_header if 'bytes_read_for_main_header' in locals() else 0)
        except Exception as e:
            logger.error(f"Unexpected error decoding header: {e}")
            return None, None, bytes_read_for_len + (bytes_read_for_main_header if 'bytes_read_for_main_header' in locals() else 0)

    def decode_payload_from_stream(self, stream_reader: ReadableStream, payload_size: int, buffer_size: int) -> Optional[bytes]:
        """
        Reads the payload of a specified size from the stream.

        Args:
            stream_reader: A stream object with a read(num_bytes) method.
            payload_size: The number of bytes to read for the payload.
            buffer_size: The chunk size for reading from the stream.

        Returns:
            The payload as bytes, or None if an error occurs or stream ends prematurely.
        """
        payload_chunks = []
        bytes_received = 0
        try:
            while bytes_received < payload_size:
                bytes_to_read = min(buffer_size, payload_size - bytes_received)
                chunk = stream_reader.read(bytes_to_read)
                if not chunk:
                    logger.error(f"Stream ended prematurely while reading payload. Expected {payload_size}, got {bytes_received}.")
                    return None
                payload_chunks.append(chunk)
                bytes_received += len(chunk)
            
            return b''.join(payload_chunks)
        except Exception as e:
            logger.error(f"Error reading payload from stream: {e}")
            return None

    def decode_message_payload(self, payload_bytes: bytes) -> Optional[str]:
        """Decodes MSG type payload bytes to string."""
        try:
            return payload_bytes.decode('utf-8')
        except UnicodeDecodeError as e:
            logger.error(f"Failed to decode message payload: {e}")
            return None

if __name__ == '__main__':
    # Example Usage (for testing ProtocolHandler)
    handler = ProtocolHandler()

    # Test encoding a message
    msg_payload = "Hello, Protocol!"
    encoded_msg = handler.encode_data(ProtocolHandler.MSG_TYPE, msg_payload)
    print(f"Encoded Message ({len(encoded_msg)} bytes): {encoded_msg}")

    # Test encoding file data (just a small byte string for example)
    file_payload_chunk = b'\x01\x02\x03\x04\x05'
    encoded_file_chunk = handler.encode_data(ProtocolHandler.FILE_TYPE, file_payload_chunk)
    print(f"Encoded File Chunk ({len(encoded_file_chunk)} bytes): {encoded_file_chunk}")

    # Test decoding (simulating a stream)
    import io

    # Simulate receiving the encoded message
    stream = io.BytesIO(encoded_msg)
    data_type, payload_size, _ = handler.decode_header_from_stream(stream)
    if data_type and payload_size is not None:
        print(f"Decoded Header: type={data_type}, payload_size={payload_size}")
        payload_bytes = handler.decode_payload_from_stream(stream, payload_size, 1024)
        if payload_bytes:
            if data_type == ProtocolHandler.MSG_TYPE:
                message = handler.decode_message_payload(payload_bytes)
                print(f"Decoded Message Payload: {message}")
            else:
                print(f"Decoded File Payload (bytes): {payload_bytes}")
    else:
        print("Failed to decode message header.")

    # Simulate receiving the encoded file chunk
    stream = io.BytesIO(encoded_file_chunk)
    data_type, payload_size, _ = handler.decode_header_from_stream(stream)
    if data_type and payload_size is not None:
        print(f"Decoded Header: type={data_type}, payload_size={payload_size}")
        payload_bytes = handler.decode_payload_from_stream(stream, payload_size, 1024)
        if payload_bytes:
            print(f"Decoded File Chunk Payload (bytes): {payload_bytes}")
    else:
        print("Failed to decode file chunk header.")
