#!/usr/bin/env python3
"""
Tunnel Data Transfer Module

This module provides a unified interface for data transfer over SSH tunnels,
supporting both simple message exchange and file transfer using the same protocol.
It abstracts the communication details to provide consistent behavior across
different types of tunnels (forward or reverse).

TODO: 
1. Asynchronous version
"""

# import os
from pathlib import Path
import socket
import threading
import time
from typing import Callable, Optional, Tuple, Union
from core.ssh_utils import BufferManager
from core.utils import build_logger
from core.progress_observer import ProgressSubject
from core.progress_events import (
    TaskStartedEvent, ProgressAdvancedEvent, TaskFinishedEvent, 
    TaskErrorEvent, generate_task_id
)

# Configure logging
logger = build_logger(__name__)

class SocketTransferSubject(ProgressSubject):
    """
    Unified class for handling data transfer over sockets using a specific protocol.
    Supports both message-based communication and file transfer.
    Extends ProgressSubject to support progress event publishing.
    """
    
    # Protocol constants
    MSG_TYPE = "MSG"
    FILE_TYPE = "FILE"
    DEFAULT_BUFFER_SIZE = 64 * 1024
    HEADER_DELIMITER = "|"
    
    def __init__(self, buffer_size: Optional[int] = None):
        """Initialize the tunnel transfer handler
        
        Args:
            buffer_size: Custom buffer size for data transfers. If None, uses DEFAULT_BUFFER_SIZE.
        """
        super().__init__()  # Initialize ProgressSubject
        self.server_socket = None
        self.running = False
        self.buffer_size = buffer_size if buffer_size is not None else self.DEFAULT_BUFFER_SIZE
        logger.debug(f"SocketTransferSubject initialized with buffer_size={self.buffer_size}")
    
    def _send_data(self, sock: socket.socket, data_type: str, data: Union[str, bytes]) -> bool:
        """
        Send data with protocol header
        
        Args:
            sock: Socket to send data through
            data_type: Type of data (MSG or FILE)
            data: Data to send (string for messages, bytes for files)
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Convert string data to bytes if needed
            if isinstance(data, str):
                data_bytes = data.encode('utf-8')
            else:
                data_bytes = data
                
            # Create header
            size = len(data_bytes)
            header = f"{data_type}{self.HEADER_DELIMITER}{size}".encode('utf-8')
            
            # Send header length (fixed 10 bytes, zero-padded)
            sock.sendall(f"{len(header):010d}".encode('utf-8'))
            
            # Send header
            sock.sendall(header)
            
            # Send data in chunks to avoid overwhelming the socket buffer
            bytes_sent = 0
            while bytes_sent < len(data_bytes):
                chunk_size = min(self.buffer_size, len(data_bytes) - bytes_sent)
                chunk = data_bytes[bytes_sent:bytes_sent + chunk_size]
                
                try:
                    sent = sock.send(chunk)
                    if sent == 0:
                        logger.error("Socket connection broken during send")
                        return False
                    bytes_sent += sent
                except socket.error as e:
                    logger.error(f"Socket error during send: {e}")
                    return False
            
            return True
        except Exception as e:
            logger.error(f"Error sending data: {e}")
            return False
    
    def _receive_data(self, sock: socket.socket) -> Tuple[Optional[str], Optional[Union[str, bytes]]]:
        """
        Receive data with protocol header
        
        Args:
            sock: Socket to receive data from
            
        Returns:
            Tuple of (data_type, data) where data is string for messages and bytes for files,
            or (None, None) if an error occurred
        """
        original_timeout = None
        try:
            # Set socket timeout for large transfers
            original_timeout = sock.gettimeout()
            sock.settimeout(30.0)  # 30 second timeout
            
            # Receive header length
            header_len_bytes = self._recv_exact(sock, 10)
            if not header_len_bytes:
                logger.error("Connection closed while receiving header length")
                return None, None
                
            header_len = int(header_len_bytes.decode('utf-8'))
            
            # Receive header
            header_bytes = self._recv_exact(sock, header_len)
            if not header_bytes:
                logger.error("Connection closed while receiving header")
                return None, None
                
            header = header_bytes.decode('utf-8')
            data_type, size_str = header.split(self.HEADER_DELIMITER)
            size = int(size_str)
            
            # Receive data
            received = 0
            chunks = []
            
            while received < size:
                chunk_size = min(self.buffer_size, size - received)
                chunk = sock.recv(chunk_size)
                
                if not chunk:
                    logger.error("Connection closed while receiving data")
                    return None, None
                    
                chunks.append(chunk)
                received += len(chunk)
                
                # # Show progress for large transfers
                # if data_type == self.FILE_TYPE and size > self.buffer_size * 10:
                #     percent = int(received * 100 / size)
                #     print(f"\rReceiving: {percent}% ({received}/{size} bytes)", end='')
            
            # Complete progress indicator
            if data_type == self.FILE_TYPE and size > self.buffer_size * 10:
                print()
                
            # Restore original timeout
            if original_timeout is not None:
                sock.settimeout(original_timeout)
                
            data = b''.join(chunks)
            
            # Convert message data to string
            if data_type == self.MSG_TYPE:
                return data_type, data.decode('utf-8')
            else:
                return data_type, data
                
        except Exception as e:
            logger.error(f"Error receiving data: {e}")
            # Restore original timeout in case of error
            try:
                if original_timeout is not None:
                    sock.settimeout(original_timeout)
            except:
                pass
            return None, None
    
    def _recv_exact(self, sock: socket.socket, num_bytes: int) -> Optional[bytes]:
        """
        Receive exactly num_bytes from socket
        
        Args:
            sock: Socket to receive from
            num_bytes: Exact number of bytes to receive
            
        Returns:
            Received bytes or None if connection closed
        """
        data = b''
        while len(data) < num_bytes:
            chunk = sock.recv(num_bytes - len(data))
            if not chunk:
                return None
            data += chunk
        return data
    
    def send_message(self, sock: socket.socket, message: str) -> bool:
        """
        Send a text message
        
        Args:
            sock: Socket to send through
            message: Text message to send
            
        Returns:
            True if successful, False otherwise
        """
        logger.debug(f"Sending message: {message}")
        return self._send_data(sock, self.MSG_TYPE, message)
    
    def receive_message(self, sock: socket.socket) -> Optional[str]:
        """
        Receive a text message
        
        Args:
            sock: Socket to receive from
            
        Returns:
            Message string or None if error or not a message
        """
        data_type, data = self._receive_data(sock)
        
        if data_type == self.MSG_TYPE and isinstance(data, str):
            logger.debug(f"Received message: {data}")
            return data
        
        return None
    
    def set_buffer_size(self, buffer_size: int) -> None:
        """Dynamically adjust the buffer size based on network conditions
        
        Args:
            buffer_size: New buffer size to use for future transfers
        """
        self.buffer_size = buffer_size
        logger.debug(f"Buffer size adjusted to {self.buffer_size} bytes")
        
    def send_file(self, sock: socket.socket, file_path: Union[str, Path]) -> bool:
        """
        Send a file
        
        Args:
            sock: Socket to send through
            file_path: Path to the file to send
            
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
        file_name = file_p.name
        
        # First send file metadata as a message
        metadata = f"{file_name}|{file_size}"
        if not self.send_message(sock, metadata):
            return False
            
        # Wait for acknowledgment
        ack = self.receive_message(sock)
        if not ack or not ack.startswith("READY"):
            logger.error(f"Did not receive proper acknowledgment. Got: {ack}")
            return False
            
        # Send file data
        try:
            sent = 0
            with file_p.open('rb') as f:
                while sent < file_size:
                    # Read chunk of data
                    chunk = f.read(self.buffer_size)
                    if not chunk:
                        break
                        
                    # Send chunk
                    if not self._send_data(sock, self.FILE_TYPE, chunk):
                        logger.error("Failed to send file chunk")
                        return False
                        
                    sent += len(chunk)
                    
                    # Show progress
                    if file_size > self.buffer_size * 10:
                        percent = int(sent * 100 / file_size)
                        print(f"\rSending: {percent}% ({sent}/{file_size} bytes)", end='')
            
            # Complete progress indicator
            if file_size > self.buffer_size * 10:
                print()
                
            # Wait for final acknowledgment
            final_ack = self.receive_message(sock)
            if not final_ack or not final_ack.startswith("SUCCESS"):
                logger.error(f"File transfer not acknowledged as successful. Got: {final_ack}")
                return False
                
            logger.info(f"File {file_name} ({file_size} bytes) sent successfully")
            return True
            
        except Exception as e:
            logger.error(f"Error sending file: {e}")
            return False
    
    def receive_file(self, sock: socket.socket, output_dir: Union[str, Path] = '.') -> Optional[str]:
        """
        Receive a file
        
        Args:
            sock: Socket to receive from
            output_dir: Directory to save the received file
            
        Returns:
            Path to the saved file or None if error
        """
        if isinstance(output_dir, str):
            output_p = Path(output_dir).expanduser()
        else:
            output_p = output_dir
        
        # Create output directory if it doesn't exist
        if not output_p.exists():
            output_p.mkdir(parents=True, exist_ok=True)
            
        # First receive file metadata
        metadata = self.receive_message(sock)
        if not metadata:
            logger.error("Failed to receive file metadata")
            return None
            
        try:
            file_name, file_size_str = metadata.split('|')
            file_size = int(file_size_str)
            
            logger.info(f"Preparing to receive file: {file_name} ({file_size} bytes)")
            
            # Send acknowledgment
            if not self.send_message(sock, "READY"):
                return None
                
            # Prepare to receive file data
            output_path = output_p / f"received_{file_name}"
            received_size = 0
            
            with output_path.open('wb') as f:
                while received_size < file_size:
                    data_type, data = self._receive_data(sock)
                    
                    if data_type != self.FILE_TYPE or not isinstance(data, bytes):
                        logger.error(f"Unexpected data type: {data_type}")
                        return None
                        
                    f.write(data)
                    received_size += len(data)
            
            # Send final acknowledgment
            if not self.send_message(sock, "SUCCESS"):
                logger.warning("Failed to send success acknowledgment")
                
            logger.info(f"File received successfully: {output_path}")
            return str(output_path)  # Convert Path to string for return type
            
        except Exception as e:
            logger.error(f"Error receiving file: {e}")
            return None
    
    def run_server(self, port: int, handler: Callable[[socket.socket], None]) -> bool:
        """
        Run a server that listens for connections and handles them with the provided function
        
        Args:
            port: Port to listen on
            handler: Function that takes a client socket and handles the communication
            
        Returns:
            True if server started successfully, False otherwise
        """
        try:
            self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server_socket.bind(('0.0.0.0', port))
            self.server_socket.listen(5)
            
            self.running = True
            logger.info(f"Server started on port {port}")
            
            while self.running:
                try:
                    client_sock, addr = self.server_socket.accept()
                    logger.info(f"Client connected from {addr}")
                    
                    # Handle client in a separate thread
                    client_thread = threading.Thread(
                        target=self._handle_client,
                        args=(client_sock, addr, handler),
                        daemon=True
                    )
                    client_thread.start()
                    
                except KeyboardInterrupt:
                    logger.info("Server interrupted")
                    self.stop_server()
                    break
                except Exception as e:
                    logger.error(f"Error accepting connection: {e}")
                    
            return True
            
        except Exception as e:
            logger.error(f"Error starting server: {e}")
            return False
    
    def _handle_client(self, client_sock: socket.socket, addr: Tuple, handler: Callable[[socket.socket], None]) -> None:
        """Handle a client connection in a thread
        
        Args:
            client_sock: Client socket
            addr: Client address
            handler: Handler function, which takes a client socket and handles the communication
        """
        try:
            handler(client_sock)
        except Exception as e:
            logger.error(f"Error handling client {addr}: {e}")
        finally:
            client_sock.close()
    
    def stop_server(self) -> None:
        """Stop the running server"""
        self.running = False
        if self.server_socket:
            self.server_socket.close()
            self.server_socket = None
        logger.info("Server stopped")
    
    def connect_to_server(self, host: str, port: int) -> Optional[socket.socket]:
        """
        Client connects to a server via socket.
        
        Args:
            host: Host to connect to
            port: Port to connect to
            
        Returns:
            Connected socket (client socket) or None if connection failed
        """
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(10)  # 10 second timeout
            sock.connect((host, port))
            logger.info(f"Connected to {host}:{port}")
            return sock
        except Exception as e:
            logger.error(f"Failed to connect to {host}:{port}: {e}")
            return None
        
    def send_file_adaptive(self, sock: socket.socket, file_path: Union[str, Path], buffer_manager: Optional[BufferManager]=None, latency: float = 0.1) -> bool:
        """
        Send a file with adaptive buffer size adjustment
        
        Args:
            sock: Socket to send through
            file_path: Path to the file to send
            buffer_manager: BufferManager instance for adaptive adjustment
            latency: Network latency in seconds
            
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
        file_name = file_p.name
        
        # Generate unique task ID for this transfer
        task_id = generate_task_id()
        
        # Notify observers that task started
        self.notify_observers(TaskStartedEvent(
            task_id=task_id,
            description=f"Sending {file_name}",
            total=file_size
        ))
        
        # First send file metadata as a message
        metadata = f"{file_name}|{file_size}"
        if not self.send_message(sock, metadata):
            self.notify_observers(TaskErrorEvent(task_id, "Failed to send file metadata"))
            return False
            
        # Wait for acknowledgment
        ack = self.receive_message(sock)
        if not ack or not ack.startswith("READY"):
            logger.error(f"Did not receive proper acknowledgment. Got: {ack}")
            self.notify_observers(TaskErrorEvent(task_id, f"Invalid acknowledgment: {ack}"))
            return False
            
        # Send file data with adaptive buffer adjustment
        try:
            sent = 0
            chunk_count = 0
            
            with file_p.open('rb') as f:
                while sent < file_size:
                    chunk_start = time.time()
                    
                    # Use current buffer size from buffer manager
                    current_buffer_size = buffer_manager.get_buffer_size() if buffer_manager else self.buffer_size
                    
                    # Read chunk of data
                    chunk = f.read(current_buffer_size)
                    if not chunk:
                        break
                        
                    # Send chunk
                    if not self._send_data(sock, self.FILE_TYPE, chunk):
                        logger.error("Failed to send file chunk")
                        self.notify_observers(TaskErrorEvent(task_id, "Failed to send file chunk"))
                        return False
                        
                    chunk_end = time.time()
                    chunk_time = chunk_end - chunk_start
                    
                    sent += len(chunk)
                    chunk_count += 1
                    
                    # Notify observers of progress
                    self.notify_observers(ProgressAdvancedEvent(task_id, advance=len(chunk)))
                    
                    # Adaptive buffer adjustment every 10 chunks
                    if buffer_manager and chunk_count % 10 == 0 and chunk_time > 0:
                        new_buffer_size = buffer_manager.adaptive_adjust(
                            len(chunk), chunk_time, latency
                        )
                        # Update socket buffer if significantly changed
                        if abs(new_buffer_size - current_buffer_size) > (current_buffer_size * 0.1):
                            try:
                                sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, new_buffer_size)
                            except Exception as e:
                                logger.debug(f"Failed to adjust socket buffer: {e}")
            
            # Wait for final acknowledgment
            final_ack = self.receive_message(sock)
            if not final_ack or not final_ack.startswith("SUCCESS"):
                logger.error(f"File transfer not acknowledged as successful. Got: {final_ack}")
                self.notify_observers(TaskErrorEvent(task_id, f"Transfer not acknowledged: {final_ack}"))
                return False
                
            # Notify observers of successful completion
            self.notify_observers(TaskFinishedEvent(
                task_id=task_id,
                description=f"Sending {file_name} [green]✓ Complete",
                success=True
            ))
            
            logger.info(f"File {file_name} ({file_size} bytes) sent successfully with adaptive buffering")
            return True
            
        except Exception as e:
            logger.error(f"Error sending file adaptively: {e}")
            self.notify_observers(TaskErrorEvent(task_id, f"Send error: {str(e)}"))
            return False
    
    def receive_file_adaptive(self, sock: socket.socket, output_dir: Union[str, Path] = '.', buffer_manager=None, latency: float = 0.1) -> Optional[str]:
        """
        Receive a file with adaptive buffer size adjustment
        
        Args:
            sock: Socket to receive from
            output_dir: Directory to save the received file
            buffer_manager: BufferManager instance for adaptive adjustment
            latency: Network latency in seconds
            
        Returns:
            Path to the saved file or None if error
        """
        if isinstance(output_dir, str):
            output_p = Path(output_dir).expanduser()
        else:
            output_p = output_dir
        
        # Create output directory if it doesn't exist
        if not output_p.exists():
            output_p.mkdir(parents=True, exist_ok=True)
            
        # First receive file metadata
        metadata = self.receive_message(sock)
        if not metadata:
            logger.error("Failed to receive file metadata")
            return None
            
        try:
            file_name, file_size_str = metadata.split('|')
            file_size = int(file_size_str)
            
            logger.info(f"Preparing to receive file: {file_name} ({file_size} bytes)")
            
            # Generate unique task ID for this transfer
            task_id = generate_task_id()
            
            # Notify observers that task started
            self.notify_observers(TaskStartedEvent(
                task_id=task_id,
                description=f"Receiving {file_name}",
                total=file_size
            ))
            
            # Send acknowledgment
            if not self.send_message(sock, "READY"):
                logger.error("Failed to send ready acknowledgment")
                self.notify_observers(TaskErrorEvent(task_id, "Failed to send ready acknowledgment"))
                return None
                
            # Prepare to receive file data with adaptive buffering
            output_path = output_p / f"received_{file_name}"
            received_size = 0
            chunk_count = 0
            
            with output_path.open('wb') as f:
                while received_size < file_size:
                    chunk_start = time.time()
                    
                    data_type, data = self._receive_data(sock)
                    
                    if data_type != self.FILE_TYPE or not isinstance(data, bytes):
                        logger.error(f"Unexpected data type: {data_type}")
                        self.notify_observers(TaskErrorEvent(task_id, f"Unexpected data type: {data_type}"))
                        return None
                        
                    f.write(data)
                    chunk_end = time.time()
                    chunk_time = chunk_end - chunk_start
                    
                    received_size += len(data)
                    chunk_count += 1
                    
                    # Notify observers of progress
                    self.notify_observers(ProgressAdvancedEvent(task_id, advance=len(data)))
                    
                    # Adaptive buffer adjustment every 10 chunks
                    if buffer_manager and chunk_count % 10 == 0 and chunk_time > 0:
                        new_buffer_size = buffer_manager.adaptive_adjust(
                            len(data), chunk_time, latency
                        )
                        # Update our internal buffer size
                        self.buffer_size = new_buffer_size
                        
                        # Update socket buffer if significantly changed
                        current_buffer = sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)
                        if abs(new_buffer_size - current_buffer) > (current_buffer * 0.1):
                            try:
                                sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, new_buffer_size)
                            except Exception as e:
                                logger.debug(f"Failed to adjust socket receive buffer: {e}")
            
            # Send final acknowledgment
            if not self.send_message(sock, "SUCCESS"):
                logger.warning("Failed to send success acknowledgment")
            
            # Notify observers of successful completion
            self.notify_observers(TaskFinishedEvent(
                task_id=task_id,
                description=f"Receiving {file_name} [green]✓ Complete",
                success=True
            ))
                
            logger.info(f"File received successfully with adaptive buffering: {output_path}")
            return str(output_path)
            
        except Exception as e:
            logger.error(f"Error receiving file adaptively: {e}")
            # task_id 可能未定义，需要检查
            try:
                self.notify_observers(TaskErrorEvent(task_id, f"Receive error: {str(e)}"))
            except NameError:
                # task_id 未定义，创建一个临时的用于错误报告
                temp_task_id = generate_task_id()
                self.notify_observers(TaskErrorEvent(temp_task_id, f"Receive error (no task): {str(e)}"))
            return None

# Example message handler function
def echo_message_handler(sock: socket.socket) -> None:
    """Example handler that echoes messages"""
    transfer = SocketTransferSubject()
    try:
        # Send welcome message
        transfer.send_message(sock, "Welcome to echo server! Send a message and I'll echo it back.")
        
        # Receive and echo
        while True:
            message = transfer.receive_message(sock)
            if not message:
                break
                
            logger.info(f"Received: {message}")
            transfer.send_message(sock, f"Echo: {message}")
            
            if message.lower() == "exit":
                break
                
    except Exception as e:
        logger.error(f"Error in echo handler: {e}")

# Example file server handler
def file_server_handler(sock: socket.socket) -> None:
    """Example handler that receives files"""
    transfer = SocketTransferSubject()
    try:
        # Send welcome message
        transfer.send_message(sock, "Welcome to file server! Send a file using the file protocol.")
        
        # Receive command
        command = transfer.receive_message(sock)
        if not command:
            return
            
        if command.startswith("SEND_FILE"):
            # Receive the file
            output_path = transfer.receive_file(sock, "received_files")
            if output_path:
                transfer.send_message(sock, f"File saved to {output_path}")
            else:
                transfer.send_message(sock, "Failed to receive file")
        else:
            transfer.send_message(sock, f"Unknown command: {command}")
            
    except Exception as e:
        logger.error(f"Error in file handler: {e}")

# Example usage (not executed when imported)
if __name__ == "__main__":
    # Example server
    transfer = SocketTransferSubject()
    
    # Start echo server
    print("Starting echo server on port 9000...")
    server_thread = threading.Thread(
        target=transfer.run_server,
        args=(9000, echo_message_handler),
        daemon=True
    )
    server_thread.start()
    
    # Start file server
    print("Starting file server on port 9001...")
    file_server_thread = threading.Thread(
        target=transfer.run_server,
        args=(9001, file_server_handler),
        daemon=True
    )
    file_server_thread.start()
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Stopping servers...")
        transfer.stop_server()
