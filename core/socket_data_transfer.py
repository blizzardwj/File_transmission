#!/usr/bin/env python3
"""
Tunnel Data Transfer Module

This module provides a unified interface for data transfer over SSH tunnels,
supporting both simple message exchange and file transfer using the same protocol.
It abstracts the communication details to provide consistent behavior across
different types of tunnels (forward or reverse).
"""

import os
import socket
import threading
import time
from typing import Callable, Optional, Tuple, Union
from core.utils import build_logger

# Configure logging
logger = build_logger(__name__)

class TunnelTransfer:
    """
    Unified class for handling data transfer over SSH tunnels.
    Supports both message-based communication and file transfer.
    """
    
    # Protocol constants
    MSG_TYPE = "MSG"
    FILE_TYPE = "FILE"
    DEFAULT_BUFFER_SIZE = 4096
    HEADER_DELIMITER = "|"
    
    def __init__(self, buffer_size: int = None):
        """Initialize the tunnel transfer handler
        
        Args:
            buffer_size: Custom buffer size for data transfers. If None, uses DEFAULT_BUFFER_SIZE.
        """
        self.server_socket = None
        self.running = False
        self.buffer_size = buffer_size if buffer_size is not None else self.DEFAULT_BUFFER_SIZE
        logger.debug(f"TunnelTransfer initialized with buffer_size={self.buffer_size}")
    
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
            
            # Send data
            sock.sendall(data_bytes)
            
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
        try:
            # Receive header length
            header_len_bytes = sock.recv(10)
            if not header_len_bytes:
                logger.error("Connection closed while receiving header length")
                return None, None
                
            header_len = int(header_len_bytes.decode('utf-8'))
            
            # Receive header
            header_bytes = sock.recv(header_len)
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
                
                # Show progress for large transfers
                if data_type == self.FILE_TYPE and size > self.buffer_size * 10:
                    percent = int(received * 100 / size)
                    print(f"\rReceiving: {percent}% ({received}/{size} bytes)", end='')
            
            # Complete progress indicator
            if data_type == self.FILE_TYPE and size > self.buffer_size * 10:
                print()
                
            data = b''.join(chunks)
            
            # Convert message data to string
            if data_type == self.MSG_TYPE:
                return data_type, data.decode('utf-8')
            else:
                return data_type, data
                
        except Exception as e:
            logger.error(f"Error receiving data: {e}")
            return None, None
    
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
        
    def send_file(self, sock: socket.socket, file_path: str) -> bool:
        """
        Send a file
        
        Args:
            sock: Socket to send through
            file_path: Path to the file to send
            
        Returns:
            True if successful, False otherwise
        """
        if not os.path.exists(file_path) or not os.path.isfile(file_path):
            logger.error(f"File not found: {file_path}")
            return False
            
        file_size = os.path.getsize(file_path)
        file_name = os.path.basename(file_path)
        
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
            with open(file_path, 'rb') as f:
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
    
    def receive_file(self, sock: socket.socket, output_dir: str = '.') -> Optional[str]:
        """
        Receive a file
        
        Args:
            sock: Socket to receive from
            output_dir: Directory to save the received file
            
        Returns:
            Path to the saved file or None if error
        """
        # Create output directory if it doesn't exist
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
            
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
            output_path = os.path.join(output_dir, f"received_{file_name}")
            received_size = 0
            
            with open(output_path, 'wb') as f:
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
            return output_path
            
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
        """Handle a client connection in a thread"""
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
        Connect to a server
        
        Args:
            host: Host to connect to
            port: Port to connect to
            
        Returns:
            Connected socket or None if connection failed
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

# Example message handler function
def echo_message_handler(sock: socket.socket) -> None:
    """Example handler that echoes messages"""
    transfer = TunnelTransfer()
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
    transfer = TunnelTransfer()
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
    transfer = TunnelTransfer()
    
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
