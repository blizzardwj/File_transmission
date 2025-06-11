#!/usr/bin/env python3
"""
Managed File Transfer Module

This module provides higher-level file transfer functionality with handshake protocols
and adaptive buffering, built on top of OptimizedSocketTransport and OptimizedProtocolHandler.
"""

import socket
import time
from pathlib import Path
from typing import Union, Optional, Tuple
from core.optimized_socket_transport import OptimizedSocketTransport
from core.optimized_protocol_handler import OptimizedProtocolHandler, ReadableStream
from core.network_utils import BufferManager
from core.utils import build_logger

logger = build_logger(__name__)

class ManagedFileTransfer:
    """
    High-level file transfer manager that implements handshake protocols
    and adaptive buffering using the optimized transport and protocol components.
    """
    
    # Message types for control flow
    MSG_TYPE_METADATA = "META"
    MSG_TYPE_ACK_READY = "READY"
    MSG_TYPE_ACK_SUCCESS = "SUCCESS"
    MSG_TYPE_ACK_ERROR = "ERROR"
    MSG_TYPE_FILEDATA = OptimizedProtocolHandler.FILE_TYPE
    MSG_TYPE_FILE_CHUNK = "CHUNK"
    MSG_TYPE_FILE_END = "END"
    
    def __init__(self, transport: OptimizedSocketTransport, 
                 protocol_handler_cls: type = OptimizedProtocolHandler):
        self.transport = transport
        self.protocol_handler = protocol_handler_cls
        self.stream: Optional[ReadableStream] = None
        
    def _get_stream(self) -> Optional[ReadableStream]:
        """Get readable stream from transport, initializing if needed."""
        if not self.stream:
            self.stream = self.transport.get_readable_stream()
        return self.stream
    
    def _send_control_message(self, message_type: str, content: str) -> bool:
        """Send a control message using the protocol handler."""
        try:
            encoded_message = self.protocol_handler.encode_data(
                message_type, content.encode('utf-8')
            )
            result = self.transport.send_all(encoded_message)
            logger.debug(f"Sent {message_type} message: {content}")
            return result
        except Exception as e:
            logger.error(f"Failed to send {message_type} message: {e}")
            return False
    
    def _receive_control_message(self, expected_type: Optional[str] = None, 
                               timeout: float = 10.0) -> Optional[Tuple[str, str]]:
        """Receive and decode a control message."""
        stream = self._get_stream()
        if not stream:
            logger.error("No readable stream available for receiving control message")
            return None
        
        try:
            decoded_data = self.protocol_handler.decode_from_stream(stream)
            if not decoded_data:
                logger.warning("Failed to decode control message from stream")
                return None
            
            data_type, payload_bytes = decoded_data
            
            # Decode payload as UTF-8 string for control messages
            try:
                payload_str = payload_bytes.decode('utf-8')
            except UnicodeDecodeError:
                logger.error(f"Could not decode payload for message type {data_type} as UTF-8")
                return None
            
            if expected_type and data_type != expected_type:
                logger.warning(f"Received unexpected message type: {data_type} (expected {expected_type}). Payload: {payload_str}")
                return None
            
            logger.debug(f"Received {data_type} message: {payload_str}")
            return data_type, payload_str
            
        except Exception as e:
            logger.error(f"Error receiving control message: {e}")
            return None
    
    def send_file_with_handshake(self, file_path: Union[str, Path], 
                               remote_filename: Optional[str] = None) -> bool:
        """
        Send a file with full handshake protocol.
        
        Args:
            file_path: Path to the file to send
            remote_filename: Optional filename to use on remote side
            
        Returns:
            True if successful, False otherwise
        """
        if isinstance(file_path, str):
            file_p = Path(file_path).expanduser()
        else:
            file_p = file_path
        
        if not file_p.exists() or not file_p.is_file():
            logger.error(f"File not found: {file_path}")
            return False
        
        file_size = file_p.stat().st_size
        actual_remote_filename = remote_filename or file_p.name
        
        try:
            # 1. Send metadata (filename|size)
            metadata_content = f"{actual_remote_filename}{self.protocol_handler.DELIMITER}{file_size}"
            logger.info(f"Sending metadata for {actual_remote_filename} ({file_size} bytes)")
            if not self._send_control_message(self.MSG_TYPE_METADATA, metadata_content):
                logger.error("Failed to send file metadata")
                return False
            
            # 2. Wait for READY acknowledgment
            logger.debug("Waiting for READY acknowledgment...")
            ack = self._receive_control_message(expected_type=self.MSG_TYPE_ACK_READY)
            if not ack:
                logger.error("Did not receive READY acknowledgment")
                return False
            logger.info(f"Received READY acknowledgment for {actual_remote_filename}")
            
            # 3. Send file data as single payload
            logger.info(f"Sending file data for {actual_remote_filename}...")
            with file_p.open('rb') as f:
                file_bytes = f.read()
            
            encoded_file_data = self.protocol_handler.encode_data(self.MSG_TYPE_FILEDATA, file_bytes)
            if not self.transport.send_all(encoded_file_data):
                logger.error("Failed to send file data")
                return False
            
            logger.debug(f"File data for {actual_remote_filename} sent successfully")
            
            # 4. Wait for SUCCESS acknowledgment
            logger.debug("Waiting for SUCCESS acknowledgment...")
            final_ack = self._receive_control_message(expected_type=self.MSG_TYPE_ACK_SUCCESS)
            if not final_ack:
                logger.error("Did not receive SUCCESS acknowledgment")
                return False
            
            logger.info(f"File {actual_remote_filename} sent successfully and acknowledged")
            return True
            
        except IOError as e:
            logger.error(f"Error reading file {file_p}: {e}")
            self._send_control_message(self.MSG_TYPE_ACK_ERROR, f"Sender IO Error: {e}")
            return False
        except Exception as e:
            logger.error(f"Error during file send with handshake: {e}")
            self._send_control_message(self.MSG_TYPE_ACK_ERROR, f"Sender Error: {e}")
            return False
    
    def receive_file_with_handshake(self, output_dir: Union[str, Path] = '.') -> Optional[Path]:
        """
        Receive a file with full handshake protocol.
        
        Args:
            output_dir: Directory to save the received file
            
        Returns:
            Path to the saved file or None if error
        """
        if isinstance(output_dir, str):
            output_p = Path(output_dir).expanduser()
        else:
            output_p = output_dir
        
        output_p.mkdir(parents=True, exist_ok=True)
        
        try:
            # 1. Receive metadata
            logger.debug("Waiting for file metadata...")
            meta_ack = self._receive_control_message(expected_type=self.MSG_TYPE_METADATA)
            if not meta_ack:
                logger.error("Failed to receive file metadata")
                return None
            
            _msg_type, meta_content = meta_ack
            try:
                file_name, file_size_str = meta_content.split(self.protocol_handler.DELIMITER, 1)
                file_size = int(file_size_str)
                logger.info(f"Received metadata: file='{file_name}', size={file_size} bytes")
            except ValueError:
                logger.error(f"Could not parse metadata: {meta_content}")
                self._send_control_message(self.MSG_TYPE_ACK_ERROR, "Invalid metadata format")
                return None
            
            # 2. Send READY acknowledgment
            logger.debug(f"Sending READY acknowledgment for {file_name}")
            if not self._send_control_message(self.MSG_TYPE_ACK_READY, f"Ready to receive {file_name}"):
                logger.error("Failed to send READY acknowledgment")
                return None
            
            # 3. Receive file data
            logger.info(f"Receiving file data for {file_name}...")
            stream = self._get_stream()
            if not stream:
                logger.error("No readable stream available for receiving file data")
                self._send_control_message(self.MSG_TYPE_ACK_ERROR, "Receiver stream error")
                return None
            
            file_data_decoded = self.protocol_handler.decode_from_stream(
                stream, buffer_size=self.transport.buffer_size
            )
            
            if not file_data_decoded:
                logger.error("Failed to decode file data from stream")
                self._send_control_message(self.MSG_TYPE_ACK_ERROR, "File data decoding failed")
                return None
            
            data_type, payload_bytes = file_data_decoded
            
            if data_type != self.MSG_TYPE_FILEDATA:
                logger.error(f"Expected file data (type {self.MSG_TYPE_FILEDATA}), but received type {data_type}")
                self._send_control_message(self.MSG_TYPE_ACK_ERROR, f"Unexpected data type {data_type}")
                return None
            
            if len(payload_bytes) != file_size:
                logger.warning(f"Received file size mismatch for {file_name}. Expected {file_size}, got {len(payload_bytes)}")
                self._send_control_message(self.MSG_TYPE_ACK_ERROR, "File size mismatch")
                return None
            
            # 4. Write file to disk
            output_file_path = output_p / file_name
            with output_file_path.open('wb') as f:
                f.write(payload_bytes)
            logger.info(f"File data written to {output_file_path}")
            
            # 5. Send SUCCESS acknowledgment
            logger.debug(f"Sending SUCCESS acknowledgment for {file_name}")
            if not self._send_control_message(self.MSG_TYPE_ACK_SUCCESS, f"Successfully received {file_name}"):
                logger.warning("Failed to send SUCCESS acknowledgment")
            
            return output_file_path
            
        except IOError as e:
            logger.error(f"Error writing received file: {e}")
            self._send_control_message(self.MSG_TYPE_ACK_ERROR, f"Receiver IO Error: {e}")
            return None
        except Exception as e:
            logger.error(f"Error during file receive with handshake: {e}")
            self._send_control_message(self.MSG_TYPE_ACK_ERROR, f"Receiver Error: {e}")
            return None
    
    def send_file_adaptive(self, file_path: Union[str, Path], 
                         buffer_manager: Optional[BufferManager] = None,
                         remote_filename: Optional[str] = None) -> bool:
        """
        Send a file with adaptive buffer management using chunked transfer.
        
        Args:
            file_path: Path to the file to send
            buffer_manager: BufferManager instance for adaptive adjustment
            remote_filename: Optional filename to use on remote side
            
        Returns:
            True if successful, False otherwise
        """
        if isinstance(file_path, str):
            file_p = Path(file_path).expanduser()
        else:
            file_p = file_path
        
        if not file_p.exists() or not file_p.is_file():
            logger.error(f"File not found: {file_path}")
            return False
        
        file_size = file_p.stat().st_size
        actual_remote_filename = remote_filename or file_p.name
        
        # Initialize buffer manager if not provided
        if buffer_manager is None:
            buffer_manager = BufferManager(self.transport.buffer_size)
        
        try:
            # 1. Send metadata (filename|size)
            metadata_content = f"{actual_remote_filename}{self.protocol_handler.DELIMITER}{file_size}"
            logger.info(f"Sending metadata for adaptive transfer: {actual_remote_filename} ({file_size} bytes)")
            if not self._send_control_message(self.MSG_TYPE_METADATA, metadata_content):
                logger.error("Failed to send file metadata")
                return False
            
            # 2. Wait for READY acknowledgment
            ack = self._receive_control_message(expected_type=self.MSG_TYPE_ACK_READY)
            if not ack:
                logger.error("Did not receive READY acknowledgment")
                return False
            
            # 3. Send file data in adaptive chunks
            logger.info(f"Starting adaptive chunked transfer for {actual_remote_filename}...")
            sent = 0
            chunk_count = 0
            
            with file_p.open('rb') as f:
                while sent < file_size:
                    chunk_start = time.time()
                    
                    # Get current buffer size from buffer manager
                    current_buffer_size = buffer_manager.get_buffer_size()
                    
                    # Read chunk of data
                    chunk = f.read(current_buffer_size)
                    if not chunk:
                        break
                    
                    # Send chunk
                    encoded_chunk = self.protocol_handler.encode_data(self.MSG_TYPE_FILE_CHUNK, chunk)
                    if not self.transport.send_all(encoded_chunk):
                        logger.error("Failed to send file chunk")
                        return False
                    
                    chunk_end = time.time()
                    chunk_time = chunk_end - chunk_start
                    
                    sent += len(chunk)
                    chunk_count += 1
                    
                    # Adaptive buffer adjustment every 10 chunks
                    if chunk_count % 10 == 0 and chunk_time > 0:
                        new_buffer_size = buffer_manager.adaptive_adjust(
                            len(chunk), chunk_time
                        )
                        # Update transport buffer size
                        self.transport.buffer_size = new_buffer_size
                        
                        # Try to adjust socket buffer if possible
                        if self.transport.sock:
                            try:
                                self.transport.sock.setsockopt(
                                    socket.SOL_SOCKET, socket.SO_SNDBUF, new_buffer_size
                                )
                            except Exception as e:
                                logger.debug(f"Failed to adjust socket send buffer: {e}")
                    
                    # Show progress for large files
                    if file_size > current_buffer_size * 10:
                        percent = int(sent * 100 / file_size)
                        rate = len(chunk) / chunk_time if chunk_time > 0 else 0
                        print(f"\rSending: {percent}% ({sent}/{file_size} bytes) - {rate/1024/1024:.2f} MB/s", end='')
            
            if file_size > buffer_manager.get_buffer_size() * 10:
                print()  # Complete progress indicator
            
            # 4. Send end-of-file marker
            if not self._send_control_message(self.MSG_TYPE_FILE_END, "EOF"):
                logger.error("Failed to send end-of-file marker")
                return False
            
            # 5. Wait for SUCCESS acknowledgment
            final_ack = self._receive_control_message(expected_type=self.MSG_TYPE_ACK_SUCCESS)
            if not final_ack:
                logger.error("Did not receive SUCCESS acknowledgment for adaptive transfer")
                return False
            
            logger.info(f"File {actual_remote_filename} sent successfully with adaptive buffering")
            return True
            
        except IOError as e:
            logger.error(f"Error reading file for adaptive transfer: {e}")
            self._send_control_message(self.MSG_TYPE_ACK_ERROR, f"Sender IO Error: {e}")
            return False
        except Exception as e:
            logger.error(f"Error during adaptive file send: {e}")
            self._send_control_message(self.MSG_TYPE_ACK_ERROR, f"Sender Error: {e}")
            return False
    
    def receive_file_adaptive(self, output_dir: Union[str, Path] = '.', 
                            buffer_manager: Optional[BufferManager] = None) -> Optional[Path]:
        """
        Receive a file with adaptive buffer management using chunked transfer.
        
        Args:
            output_dir: Directory to save the received file
            buffer_manager: BufferManager instance for adaptive adjustment
            
        Returns:
            Path to the saved file or None if error
        """
        if isinstance(output_dir, str):
            output_p = Path(output_dir).expanduser()
        else:
            output_p = output_dir
        
        output_p.mkdir(parents=True, exist_ok=True)
        
        # Initialize buffer manager if not provided
        if buffer_manager is None:
            buffer_manager = BufferManager(self.transport.buffer_size)
        
        try:
            # 1. Receive metadata
            logger.debug("Waiting for file metadata for adaptive transfer...")
            meta_ack = self._receive_control_message(expected_type=self.MSG_TYPE_METADATA)
            if not meta_ack:
                logger.error("Failed to receive file metadata")
                return None
            
            _msg_type, meta_content = meta_ack
            try:
                file_name, file_size_str = meta_content.split(self.protocol_handler.DELIMITER, 1)
                file_size = int(file_size_str)
                logger.info(f"Preparing for adaptive receive: file='{file_name}', size={file_size} bytes")
            except ValueError:
                logger.error(f"Could not parse metadata: {meta_content}")
                self._send_control_message(self.MSG_TYPE_ACK_ERROR, "Invalid metadata format")
                return None
            
            # 2. Send READY acknowledgment
            if not self._send_control_message(self.MSG_TYPE_ACK_READY, f"Ready to receive {file_name}"):
                logger.error("Failed to send READY acknowledgment")
                return None
            
            # 3. Receive file data in chunks
            logger.info(f"Starting adaptive chunked receive for {file_name}...")
            output_file_path = output_p / file_name
            received_size = 0
            chunk_count = 0
            
            stream = self._get_stream()
            if not stream:
                logger.error("No readable stream available")
                self._send_control_message(self.MSG_TYPE_ACK_ERROR, "Receiver stream error")
                return None
            
            with output_file_path.open('wb') as f:
                while received_size < file_size:
                    chunk_start = time.time()
                    
                    # Receive next chunk or end marker
                    decoded_data = self.protocol_handler.decode_from_stream(stream)
                    if not decoded_data:
                        logger.error("Failed to decode chunk data from stream")
                        self._send_control_message(self.MSG_TYPE_ACK_ERROR, "Chunk decoding failed")
                        return None
                    
                    data_type, payload_bytes = decoded_data
                    
                    if data_type == self.MSG_TYPE_FILE_END:
                        logger.debug("Received end-of-file marker")
                        break
                    elif data_type == self.MSG_TYPE_FILE_CHUNK:
                        f.write(payload_bytes)
                        chunk_end = time.time()
                        chunk_time = chunk_end - chunk_start
                        
                        received_size += len(payload_bytes)
                        chunk_count += 1
                        
                        # Adaptive buffer adjustment every 10 chunks
                        if chunk_count % 10 == 0 and chunk_time > 0:
                            new_buffer_size = buffer_manager.adaptive_adjust(
                                len(payload_bytes), chunk_time
                            )
                            # Update transport buffer size
                            self.transport.buffer_size = new_buffer_size
                            
                            # Try to adjust socket receive buffer if possible
                            if self.transport.sock:
                                try:
                                    self.transport.sock.setsockopt(
                                        socket.SOL_SOCKET, socket.SO_RCVBUF, new_buffer_size
                                    )
                                except Exception as e:
                                    logger.debug(f"Failed to adjust socket receive buffer: {e}")
                        
                        # Show progress for large files
                        if file_size > buffer_manager.get_buffer_size() * 10:
                            percent = int(received_size * 100 / file_size)
                            rate = len(payload_bytes) / chunk_time if chunk_time > 0 else 0
                            print(f"\rReceiving: {percent}% ({received_size}/{file_size} bytes) - {rate/1024/1024:.2f} MB/s", end='')
                    else:
                        logger.error(f"Unexpected chunk data type: {data_type}")
                        self._send_control_message(self.MSG_TYPE_ACK_ERROR, f"Unexpected data type {data_type}")
                        return None
            
            if file_size > buffer_manager.get_buffer_size() * 10:
                print()  # Complete progress indicator
            
            logger.info(f"File data written to {output_file_path}")
            
            # 4. Send SUCCESS acknowledgment
            if not self._send_control_message(self.MSG_TYPE_ACK_SUCCESS, f"Successfully received {file_name}"):
                logger.warning("Failed to send SUCCESS acknowledgment")
            
            logger.info(f"File received successfully with adaptive buffering: {output_file_path}")
            return output_file_path
            
        except IOError as e:
            logger.error(f"Error writing received file during adaptive transfer: {e}")
            self._send_control_message(self.MSG_TYPE_ACK_ERROR, f"Receiver IO Error: {e}")
            return None
        except Exception as e:
            logger.error(f"Error during adaptive file receive: {e}")
            self._send_control_message(self.MSG_TYPE_ACK_ERROR, f"Receiver Error: {e}")
            return None
    
    def send_message(self, message: str) -> bool:
        """Send a simple text message."""
        return self._send_control_message(OptimizedProtocolHandler.MSG_TYPE, message)
    
    def receive_message(self) -> Optional[str]:
        """Receive a simple text message."""
        result = self._receive_control_message(expected_type=OptimizedProtocolHandler.MSG_TYPE)
        if result:
            return result[1]
        return None


# Example usage functions
def create_file_server_handler(output_dir: str = "received_files"):
    """Create a handler function for file server operations."""
    def file_server_handler(transport: OptimizedSocketTransport, addr: Tuple):
        """Example handler that receives files with handshake."""
        logger.info(f"File server handling client: {addr}")
        manager = ManagedFileTransfer(transport)
        
        try:
            # Send welcome message
            manager.send_message("Welcome to managed file server! Send a file using the file protocol.")
            
            # Receive command
            command = manager.receive_message()
            if not command:
                return
            
            if command.startswith("SEND_FILE"):
                # Receive the file with handshake
                output_path = manager.receive_file_with_handshake(output_dir)
                if output_path:
                    manager.send_message(f"File saved to {output_path}")
                else:
                    manager.send_message("Failed to receive file")
            elif command.startswith("SEND_FILE_ADAPTIVE"):
                # Receive the file with adaptive buffering
                output_path = manager.receive_file_adaptive(output_dir)
                if output_path:
                    manager.send_message(f"File saved adaptively to {output_path}")
                else:
                    manager.send_message("Failed to receive file adaptively")
            else:
                manager.send_message(f"Unknown command: {command}")
                
        except Exception as e:
            logger.error(f"Error in file server handler: {e}")
    
    return file_server_handler


if __name__ == "__main__":
    import socket
    import threading
    
    # Test the managed file transfer functionality
    def test_client():
        """Test client that sends a file."""
        time.sleep(0.5)  # Wait for server to start
        
        transport = OptimizedSocketTransport()
        if transport.connect("127.0.0.1", 9007):
            manager = ManagedFileTransfer(transport)
            
            # First receive welcome message
            welcome = manager.receive_message()
            print(f"Client: Received welcome: {welcome}")
            
            # Send command 
            manager.send_message("SEND_FILE")
            
            # Create a test file
            test_file = Path("test_managed_transfer.txt")
            test_file.write_text("Hello from managed file transfer!\nThis is a test file.")
            
            if manager.send_file_with_handshake(test_file):
                print("Client: File sent successfully with handshake!")
                response = manager.receive_message()
                print(f"Server response: {response}")
            else:
                print("Client: File send failed")
            
            transport.close()
            test_file.unlink()  # Clean up
    
    # Start test server
    server = OptimizedSocketTransport()
    handler = create_file_server_handler("test_received")
    
    server_thread = threading.Thread(
        target=server.start_server,
        args=("127.0.0.1", 9007, handler),
        daemon=True
    )
    server_thread.start()
    print("Test managed file server started on port 9007")
    
    # Start test client
    client_thread = threading.Thread(target=test_client, daemon=True)
    client_thread.start()
    
    # Wait for test to complete
    client_thread.join(timeout=10)
    server.stop_server()
    print("Test completed")
