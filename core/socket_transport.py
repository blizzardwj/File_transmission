#!/usr/bin/env python3
"""
Socket Transport Module

This module provides the SocketTransport class, responsible for low-level
socket operations like connecting, sending, receiving, and managing
socket server lifecycle.
"""
import socket
import threading
from typing import Optional, Tuple, Callable # Removed BinaryIO here
from core.utils import build_logger
from core.protocol_handler import ReadableStream # Import the new protocol

logger = build_logger(__name__)

class SocketTransport:
    """
    Handles low-level socket communication (connect, send, receive, listen).
    """
    DEFAULT_BUFFER_SIZE = 64 * 1024  # Default chunk size for sending/receiving

    def __init__(self, host: Optional[str] = None, port: Optional[int] = None, buffer_size: Optional[int] = None):
        self.host = host
        self.port = port
        self.sock: Optional[socket.socket] = None
        self.server_socket: Optional[socket.socket] = None
        self.running = False
        self.buffer_size = buffer_size if buffer_size is not None else self.DEFAULT_BUFFER_SIZE
        logger.debug(f"SocketTransport initialized. Host: {host}, Port: {port}, Buffer: {self.buffer_size}")

    def connect(self, host: Optional[str] = None, port: Optional[int] = None, timeout: float = 10.0) -> bool:
        """
        Connects to a remote server.

        Args:
            host: The host to connect to. Uses instance host if None.
            port: The port to connect to. Uses instance port if None.
            timeout: Connection timeout in seconds.

        Returns:
            True if connection is successful, False otherwise.
        """
        target_host = host or self.host
        target_port = port or self.port

        if not target_host or not target_port:
            logger.error("Host and port must be provided either at init or during connect.")
            return False

        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(timeout)
            self.sock.connect((target_host, target_port))
            logger.info(f"Successfully connected to {target_host}:{target_port}")
            # Make the socket behave like a BinaryIO for protocol handler
            self.sock_file_obj = self.sock.makefile('rb', buffering=0) # Unbuffered for reading
            return True
        except socket.timeout:
            logger.error(f"Connection to {target_host}:{target_port} timed out after {timeout}s.")
            self.sock = None
            return False
        except socket.error as e:
            logger.error(f"Failed to connect to {target_host}:{target_port}: {e}")
            self.sock = None
            return False
        except Exception as e:
            logger.error(f"An unexpected error occurred during connect: {e}")
            self.sock = None
            return False

    def send_all(self, data: bytes) -> bool:
        """
        Sends all provided data through the connected socket.

        Args:
            data: The bytes to send.

        Returns:
            True if all data was sent successfully, False otherwise.
        """
        if not self.sock:
            logger.error("Socket not connected. Cannot send data.")
            return False
        
        total_sent = 0
        try:
            while total_sent < len(data):
                sent = self.sock.send(data[total_sent:])
                if sent == 0:
                    logger.error("Socket connection broken during send_all.")
                    return False
                total_sent += sent
            logger.debug(f"Successfully sent {total_sent} bytes.")
            return True
        except socket.error as e:
            logger.error(f"Socket error during send_all: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error during send_all: {e}")
            return False

    def receive_exact(self, num_bytes: int) -> Optional[bytes]:
        """
        Receives exactly num_bytes from the connected socket.
        This method is kept for direct use if needed, but ProtocolHandler
        will typically use the file-like object from sock.makefile('rb').

        Args:
            num_bytes: The exact number of bytes to receive.

        Returns:
            The received bytes, or None if the connection is closed or an error occurs.
        """
        if not self.sock:
            logger.error("Socket not connected. Cannot receive data.")
            return None
        
        data_chunks = []
        bytes_received = 0
        try:
            while bytes_received < num_bytes:
                chunk = self.sock.recv(min(num_bytes - bytes_received, self.buffer_size))
                if not chunk:
                    logger.error("Socket connection broken while receiving data (receive_exact).")
                    return None
                data_chunks.append(chunk)
                bytes_received += len(chunk)
            return b''.join(data_chunks)
        except socket.timeout:
            logger.warning("Socket timeout during receive_exact.")
            return None # Or potentially return partial data if that's desired
        except socket.error as e:
            logger.error(f"Socket error during receive_exact: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error during receive_exact: {e}")
            return None

    def get_readable_stream(self) -> Optional[ReadableStream]:
        """
        Returns a file-like object (conforming to ReadableStream) for reading from the socket.
        This is the preferred way for ProtocolHandler to read.
        """
        if not self.sock:
            logger.error("Socket not connected. Cannot get readable stream.")
            return None
        
        # Ensure self.sock_file_obj is initialized and is a socket.SocketIO
        # which structurally matches ReadableStream for the 'read' method.
        # Type hint for self.sock_file_obj should be Optional[socket.SocketIO] if declared in __init__
        # or it can be inferred here.
        if not hasattr(self, 'sock_file_obj') or \
           (hasattr(self.sock_file_obj, 'closed') and self.sock_file_obj.closed):
            try:
                # self.sock should be a valid socket here
                # Explicitly type the created object if needed for clarity or strict linters
                created_sock_file_obj: socket.SocketIO = self.sock.makefile('rb', buffering=0)
                self.sock_file_obj = created_sock_file_obj
            except Exception as e:
                logger.error(f"Failed to create readable stream from socket: {e}")
                return None
        return self.sock_file_obj

    def close(self) -> None:
        """Closes the client socket connection."""
        if hasattr(self, 'sock_file_obj') and self.sock_file_obj and \
           hasattr(self.sock_file_obj, 'closed') and hasattr(self.sock_file_obj, 'close'):
            try:
                if not self.sock_file_obj.closed:
                    self.sock_file_obj.close()
            except Exception as e:
                logger.debug(f"Error closing socket file object: {e}")
        if self.sock:
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except socket.error as e:
                logger.debug(f"Socket shutdown error (client): {e}") # Common if already closed
            finally:
                self.sock.close()
                self.sock = None
                logger.info("Client socket closed.")
        
        if self.server_socket: # Also ensure server socket is closed if this instance was a server
            self.stop_server()

    def start_server(self, port: int, client_handler_func: Callable[['SocketTransport', socket.socket, Tuple], None], host: str = '0.0.0.0') -> bool:
        """
        Starts a server listening on the given host and port.

        Args:
            port: The port to listen on.
            client_handler_func: A function to be called when a new client connects.
                                 It receives the SocketTransport instance (for the new client), 
                                 the client socket, and the client address.
            host: The host to bind to (default '0.0.0.0').

        Returns:
            True if the server started successfully, False otherwise.
        """
        self.host = host
        self.port = port
        try:
            self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server_socket.bind((self.host, self.port))
            self.server_socket.listen(5)
            self.running = True
            logger.info(f"Server started on {self.host}:{self.port}")

            while self.running:
                try:
                    client_sock, addr = self.server_socket.accept()
                    logger.info(f"Client connected from {addr}")
                    
                    # Create a new SocketTransport instance for this client connection
                    client_transport = SocketTransport(buffer_size=self.buffer_size)
                    client_transport.sock = client_sock
                    # Ensure the file-like object is created for the new client socket
                    client_transport.sock_file_obj = client_sock.makefile('rb', buffering=0)


                    # Handle client in a separate thread
                    # The handler function will use this new client_transport
                    thread = threading.Thread(
                        target=client_handler_func,
                        args=(client_transport, addr), # Pass client_transport and addr
                        daemon=True
                    )
                    thread.start()
                except socket.error as e:
                    if self.running: # Only log if we weren't intentionally stopping
                        logger.error(f"Error accepting connection: {e}")
                    break # Exit loop if server socket has issues (e.g. closed)
                except Exception as e:
                    if self.running:
                        logger.error(f"Unexpected error in server loop: {e}")
            return True
        except Exception as e:
            logger.error(f"Failed to start server on {self.host}:{self.port}: {e}")
            self.running = False
            if self.server_socket:
                self.server_socket.close()
                self.server_socket = None
            return False

    def stop_server(self) -> None:
        """Stops the running server."""
        self.running = False
        if self.server_socket:
            logger.info("Stopping server...")
            try:
                # To unblock server_socket.accept()
                # This is a common way, though platform behavior can vary.
                # Connecting to it locally can help unblock accept()
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.settimeout(0.1) # very short timeout
                    try:
                        s.connect((self.host if self.host != '0.0.0.0' else '127.0.0.1', self.port))
                    except: 
                        pass # Ignore errors, just trying to unblock
                self.server_socket.close()
            except socket.error as e:
                logger.debug(f"Error closing server socket: {e}")
            finally:
                self.server_socket = None
                logger.info("Server stopped.")

    def set_buffer_size(self, buffer_size: int):
        """Dynamically adjust the buffer size for socket operations."""
        self.buffer_size = buffer_size
        logger.debug(f"SocketTransport buffer size set to {self.buffer_size}")
        # Note: This sets the internal buffer for recv chunking.
        # For SO_SNDBUF/SO_RCVBUF, one would use setsockopt on self.sock directly.

if __name__ == '__main__':
    # Example Usage (for testing SocketTransport)
    
    # Test Server
    def simple_client_handler(client_transport: SocketTransport, address: Tuple):
        logger.info(f"Simple handler: New client from {address}")
        try:
            # Example: receive a piece of data using the client_transport's stream
            stream = client_transport.get_readable_stream()
            if stream:
                # This is a raw read, not using protocol handler here for simplicity
                data = stream.read(100) 
                if data:
                    logger.info(f"Simple handler received {len(data)} raw bytes: {data[:20]}...")
                    # Echo back
                    client_transport.send_all(b"ECHO: " + data)
                else:
                    logger.info("Simple handler: No data received or client disconnected.")
        except Exception as e:
            logger.error(f"Simple handler error for {address}: {e}")
        finally:
            logger.info(f"Simple handler: Closing connection for {address}")
            client_transport.close()

    server_transport = SocketTransport()
    server_thread = threading.Thread(
        target=server_transport.start_server,
        args=(9005, simple_client_handler),
        daemon=True
    )
    server_thread.start()
    print("Test server started on port 9005. Connect to it to test.")

    # Test Client
    client_transport = SocketTransport()
    if client_transport.connect("127.0.0.1", 9005):
        print("Client connected. Sending data.")
        client_transport.send_all(b"Hello from client!")
        
        # Example: receive using the stream
        client_stream = client_transport.get_readable_stream()
        if client_stream:
            response = client_stream.read(100) # Raw read
            if response:
                print(f"Client received raw response: {response.decode(errors='ignore')}")
        
        client_transport.close()
    else:
        print("Client failed to connect.")

    print("Stopping server (may take a moment for thread to exit)...")
    server_transport.stop_server()
    server_thread.join(timeout=2) # Wait for server thread to finish
    print("Test finished.")
