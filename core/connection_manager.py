"""connection_manager.py

Connection lifecycle management for socket-based communication.
This module handles the establishment, management, and teardown of network connections,
working alongside the layered transfer protocol from layered_transfer.py.

This module separates connection management concerns from the data transfer protocol,
maintaining clean separation of responsibilities in accordance with SOLID principles.

Author: Refactored from SocketTransferSubject
"""
from __future__ import annotations

import socket
import threading
from typing import Callable, Optional, Tuple
from abc import ABC, abstractmethod

from core.utils import build_logger
from core.layered_transfer import Transport, SocketTransport

logger = build_logger(__name__)


class ConnectionManager(ABC):
    """Abstract base class for connection management."""
    
    @abstractmethod
    def connect_to_server(self, host: str, port: int) -> Optional[Transport]:
        """Connect to a server and return a Transport instance."""
        pass
    
    @abstractmethod
    def run_server(self, port: int, handler: Callable[[Transport, Tuple], None]) -> bool:
        """Run a server that accepts connections and handles them."""
        pass
    
    @abstractmethod
    def stop_server(self) -> None:
        """Stop the running server."""
        pass


class SocketConnectionManager(ConnectionManager):
    """Socket-based implementation of connection management.
    
    This class handles the lifecycle of TCP socket connections, including:
    - Client connection establishment
    - Server socket creation and management
    - Multi-threaded client handling
    - Graceful shutdown
    """
    
    def __init__(self, connection_timeout: float = 10.0, socket_timeout: float = 30.0):
        """Initialize the connection manager.
        
        Args:
            connection_timeout: Timeout for initial connection establishment
            socket_timeout: Timeout for socket operations after connection
        """
        self._connection_timeout = connection_timeout
        self._socket_timeout = socket_timeout
        self._server_socket: Optional[socket.socket] = None
        self._running = False
        self._client_threads: list[threading.Thread] = []
    
    def connect_to_server(self, host: str, port: int) -> Optional[Transport]:
        """Connect to a server and return a SocketTransport instance.
        
        Args:
            host: Host to connect to
            port: Port to connect to
            
        Returns:
            SocketTransport instance or None if connection failed
        """
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self._connection_timeout)
            sock.connect((host, port))
            logger.info(f"Connected to {host}:{port}")
            
            # Create and return SocketTransport with configured timeout
            transport = SocketTransport(sock, timeout=self._socket_timeout)
            return transport
            
        except Exception as e:
            logger.error(f"Failed to connect to {host}:{port}: {e}")
            if sock:
                try:
                    sock.close()
                except:
                    pass
            return None
    
    def run_server(self, port: int, handler: Callable[[Transport, Tuple], None]) -> bool:
        """Run a server that listens for connections and handles them.
        
        The handler function should accept two parameters:
        - transport: A Transport instance for communication
        - addr: Client address tuple (host, port)
        
        Args:
            port: Port to listen on
            handler: Function that handles client connections
            
        Returns:
            True if server started successfully, False otherwise
        """
        try:
            # 1. Create sever socket
            self._server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            # 2. Bind to specificed port
            self._server_socket.bind(('0.0.0.0', port))
            # 3. Listen for connections
            self._server_socket.listen(5)

            self._running = True
            logger.info(f"Server started on port {port}")
            
            while self._running:
                try:
                    # 4. Accept incoming connections
                    client_sock, addr = self._server_socket.accept()
                    logger.info(f"Client connected from {addr}")
                    
                    # 5. Handle client in a separate thread
                    client_thread = threading.Thread(
                        target=self._handle_client,
                        args=(client_sock, addr, handler),
                        daemon=True
                    )
                    client_thread.start()
                    self._client_threads.append(client_thread)
                    
                    # Clean up finished threads
                    self._cleanup_finished_threads()
                    
                except KeyboardInterrupt:
                    logger.info("Server interrupted")
                    self.stop_server()
                    break
                except Exception as e:
                    if self._running:  # Only log if we're still supposed to be running
                        logger.error(f"Error accepting connection: {e}")
                        
            return True
            
        except Exception as e:
            logger.error(f"Error starting server: {e}")
            return False
    
    def _handle_client(self, 
        client_sock: socket.socket, 
        addr: Tuple, 
        handler: Callable[[Transport, Tuple], None]
    ) -> None:
        """Handle a client connection in a thread.
        
        Args:
            client_sock: Client socket
            addr: Client address
            handler: Handler function for the client
        """
        transport = None
        try:
            # Create transport for this client
            transport = SocketTransport(client_sock, timeout=self._socket_timeout)
            
            # Call the user-provided handler
            handler(transport, addr)
            
        except Exception as e:
            logger.error(f"Error handling client {addr}: {e}")
        finally:
            # Ensure transport and socket are properly closed
            if transport:
                transport.close()
            else:
                try:
                    client_sock.close()
                except:
                    pass
    
    def _cleanup_finished_threads(self) -> None:
        """Remove finished threads from the active threads list."""
        self._client_threads = [t for t in self._client_threads if t.is_alive()]
    
    def stop_server(self) -> None:
        """Stop the running server and clean up resources."""
        self._running = False
        
        if self._server_socket:
            try:
                self._server_socket.close()
            except:
                pass
            self._server_socket = None
        
        # Wait for active client threads to finish (with timeout)
        for thread in self._client_threads:
            if thread.is_alive():
                thread.join(timeout=1.0)
        
        self._client_threads.clear()
        logger.info("Server stopped")
    
    @property
    def is_running(self) -> bool:
        """Check if the server is currently running."""
        return self._running
    
    @property
    def active_connections(self) -> int:
        """Get the number of active client connections."""
        self._cleanup_finished_threads()
        return len(self._client_threads)


# Factory function for easy instantiation
def create_socket_connection_manager(
    connection_timeout: float = 10.0,
    socket_timeout: float = 30.0
) -> SocketConnectionManager:
    """Factory function to create a SocketConnectionManager with specified timeouts.
    
    Args:
        connection_timeout: Timeout for initial connection establishment
        socket_timeout: Timeout for socket operations after connection
        
    Returns:
        Configured SocketConnectionManager instance
    """
    return SocketConnectionManager(connection_timeout, socket_timeout)


# Example handler functions that work with the new architecture
def echo_handler(transport: Transport, addr: Tuple) -> None:
    """Example handler that echoes messages back to the client.
    
    Args:
        transport: Transport instance for communication
        addr: Client address
    """
    from core.layered_transfer import MessageService
    
    try:
        msg_service = MessageService(transport)
        
        # Send welcome message
        msg_service.send("Welcome to echo server! Send a message and I'll echo it back.")
        
        # Echo messages until client disconnects or sends "exit"
        while True:
            try:
                message = msg_service.recv()
                logger.info(f"Received from {addr}: {message}")
                
                if message.lower() == "exit":
                    msg_service.send("Goodbye!")
                    break
                
                msg_service.send(f"Echo: {message}")
                
            except Exception as e:
                logger.info(f"Client {addr} disconnected: {e}")
                break
                
    except Exception as e:
        logger.error(f"Error in echo handler for {addr}: {e}")


def file_transfer_handler(transport: Transport, addr: Tuple) -> None:
    """Example handler that receives files from clients.
    
    Args:
        transport: Transport instance for communication
        addr: Client address
    """
    from core.layered_transfer import MessageService, FileTransferService
    from pathlib import Path
    
    try:
        msg_service = MessageService(transport)
        file_service = FileTransferService(transport)
        
        # Send welcome message
        msg_service.send("Welcome to file server! Send 'RECEIVE_FILE' to upload a file.")
        
        # Wait for command
        command = msg_service.recv()
        logger.info(f"Received command from {addr}: {command}")
        
        if command.upper() == "RECEIVE_FILE":
            # Prepare to receive file
            output_dir = Path("received_files")
            try:
                received_path = file_service.receive_file(output_dir)
                msg_service.send(f"File saved to {received_path}")
                logger.info(f"File received from {addr}: {received_path}")
            except Exception as e:
                error_msg = f"Failed to receive file: {e}"
                msg_service.send(error_msg)
                logger.error(f"File transfer error from {addr}: {e}")
        else:
            msg_service.send(f"Unknown command: {command}")
            
    except Exception as e:
        logger.error(f"Error in file transfer handler for {addr}: {e}")


__all__ = [
    "ConnectionManager",
    "SocketConnectionManager", 
    "create_socket_connection_manager",
    "echo_handler",
    "file_transfer_handler",
]
