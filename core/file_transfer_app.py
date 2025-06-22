#!/usr/bin/env python3
"""
File Transfer Application with Reverse SSH Tunnel Support

Refactored from experiments/reverse_ssh_tunnel.py
Make it more modular and reusable, and can load both configurations from config_sender.yaml and config_receiver.yaml

This application establishes a reverse SSH tunnel and a forward SSH tunnel to facilitate file transfers between a sender and a receiver through a jump server.
1. Sender Mode: Connects to jump server and sends files through the forward tunnel
2. Receiver Mode: Runs a local server and receives files through the reverse tunnel

Usage:
    python file_transfer_app.py --config config_receiver.yml  # Run this first on receiver machine
    python file_transfer_app.py --config config_sender.yml    # Run this next on sender machine


Configuration is loaded from YAML files that specify:
- SSH connection details (jump server, credentials, ports)
- Operation mode (sender/receiver)
- File paths and transfer settings
- Progress observer options

NOTE:
- If the transmission is interrupted passively by network issues or other factors, it is better to close the terminal and restart the application. Because sometimes the listening port is not released on the jump server, even if the threads on both sender and receiver are stopped.
- If the transmission is interrupted actively by user (e.g., Ctrl+c), the application will try to stop the listening port on the jump server by cleaning up the threads and closing the SSH connections. So the listening port should be released properly.
"""

import os
import sys
import time
import yaml
from pathlib import Path
import socket
import argparse
import threading
import getpass
from typing import Optional, Dict, Any

# Add parent directory to path to import core modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.utils import build_logger
from core.ssh_utils import SSHConfig, SSHTunnelForward, SSHTunnelReverse
from core.network_utils import BufferManager
from core.socket_transfer_subject import SocketTransferSubject

# Import observer-related modules
from core.progress_observer import IProgressObserver
try:
    from core.rich_progress_observer import create_progress_observer
    from core.utils import get_shared_console
    RICH_AVAILABLE = True
    print("✓ Rich is available, using RichProgressObserver for progress tracking")
    shared_console = get_shared_console()
except ImportError as e:
    print(f"✗ Rich not available ({e}), using fallback observer")
    from core.rich_progress_observer import create_progress_observer
    RICH_AVAILABLE = False
    shared_console = None

logger = build_logger(__name__)

class ObserverContext:
    """
    Context manager for automatic observer management
    
    Ensures that observers are properly added and removed from SocketTransferSubject instances,
    and manages the observer's lifecycle (start/stop) if the observer supports it.
    This ensures proper cleanup even if exceptions occur during the transfer operations.
    """
    
    def __init__(self, 
        subject: SocketTransferSubject, 
        observer: Optional['IProgressObserver'] = None
    ):
        """
        Initialize the observer context
        
        Args:
            subject: SocketTransferSubject instance to manage
            observer: Observer instance implementing IProgressObserver interface or None
        """
        self.subject = subject
        self.observer = observer
        self._observer_added = False
        self._observer_started = False
    
    def __enter__(self):
        """Context manager entry - start observer and add to subject"""
        if self.observer:
            # Start the observer if it has a start() method
            if hasattr(self.observer, 'start') and callable(getattr(self.observer, 'start')):
                try:
                    self.observer.start()
                    self._observer_started = True
                    logger.debug(f"Observer {self.observer.__class__.__name__} started")
                except Exception as e:
                    logger.warning(f"Failed to start observer {self.observer.__class__.__name__}: {e}")
            
            # Add observer to subject
            self.subject.add_observer(self.observer)
            self._observer_added = True
            logger.debug(f"Observer {self.observer.__class__.__name__} added to transfer subject")
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - remove observer from subject and stop observer"""
        if self.observer:
            # Remove observer from subject
            if self._observer_added:
                self.subject.remove_observer(self.observer)
                self._observer_added = False
                logger.debug(f"Observer {self.observer.__class__.__name__} removed from transfer subject")
            
            # Stop the observer if it was started and has a stop() method
            if self._observer_started and hasattr(self.observer, 'stop') and callable(getattr(self.observer, 'stop')):
                try:
                    self.observer.stop()
                    self._observer_started = False
                    if hasattr(self.observer, 'has_living_observers') and not self.observer.has_living_observers:
                        logger.debug(f"Observer {self.observer.__class__.__name__} stopped")
                    else:
                        logger.debug(f"Observer {self.observer.__class__.__name__} has remaining tasks, not stopping")
                except Exception as e:
                    logger.warning(f"Failed to stop observer {self.observer.__class__.__name__}: {e}")
        
        return False  # Don't suppress exceptions


class FileTransferApp:
    """
    Main application class for file transfer through reverse SSH tunnel
    """
    
    def __init__(self, config_path: str):
        """
        Initialize the application with configuration from YAML file
        
        Args:
            config_path: Path to YAML configuration file
        """
        self.config = self._load_config(config_path)
        self.ssh_config = self._create_ssh_config()
        self.reverse_tunnel = None
        self.forward_tunnel = None
        self.server_thread = None
        
    def _load_config(self, config_path: str) -> Dict[str, Any]:
        """Load configuration from YAML file"""
        config_file = Path(config_path).expanduser()
        if not config_file.exists():
            raise FileNotFoundError(f"Configuration file not found: {config_path}")
            
        with open(config_file, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
            
        logger.info(f"Loaded configuration from {config_path}")
        return config
        
    def _create_ssh_config(self) -> SSHConfig:
        """Create SSH configuration from loaded config"""
        ssh_section = self.config['ssh']
        
        ssh_config = SSHConfig(
            jump_server=ssh_section['jump_server'],
            jump_user=ssh_section['jump_user'],
            jump_port=ssh_section.get('jump_port', 22),
            identity_file=ssh_section.get('identity_file'),
            use_password=ssh_section.get('use_password', False)
        )
        
        # Handle password authentication
        if ssh_config.use_password:
            password = ssh_section.get('password')
            if password:
                ssh_config.password = password
            else:
                ssh_config.password = getpass.getpass(
                    f"Password for {ssh_config.jump_user}@{ssh_config.jump_server}: "
                )
                
        return ssh_config
        
    def _create_observer_if_enabled(self, console=None) -> Optional[IProgressObserver]:
        """
        Create a progress observer if enabled in configuration
        
        Args:
            console: Rich Console instance (optional)
            
        Returns:
            IProgressObserver instance or None if disabled
        """
        progress_config = self.config.get('progress', {})
        if not progress_config.get("use_progress_observer", False):
            return None
        
        try:
            use_rich = progress_config.get("use_rich_progress", True) and RICH_AVAILABLE
            return create_progress_observer(use_rich=use_rich, shared_mode=True, console=console)
        except Exception as e:
            logger.warning(f"Failed to create progress observer: {e}")
            return None
            
    def _message_server_handler(self, sock: socket.socket) -> None:
        """
        Handle a client connection for message exchange server
        This runs on the local machine and receives connections via the jump server
        """
        transfer = SocketTransferSubject()
        try:
            # Send welcome message
            transfer.send_message(sock, "Hello from the local machine! This is a message exchange service accessed via reverse tunnel.")
            
            # Receive and respond to messages
            while True:
                message = transfer.receive_message(sock)
                if not message:
                    logger.info("Client disconnected")
                    break
                    
                logger.info(f"Received message via reverse tunnel: {message}")
                
                # Echo back the data with a prefix
                transfer.send_message(sock, f"Echo from local machine: {message}")
                
                # Exit if requested
                if message.lower() in ["exit", "quit", "bye"]:
                    break
                    
        except Exception as e:
            logger.error(f"Error in message server handler: {e}")
            
    def _file_server_handler(self, sock: socket.socket) -> None:
        """
        Handle a client connection for file exchange server
        This runs on the local machine and receives connections via the jump server
        """
        transfer = SocketTransferSubject()
        buffer_manager = BufferManager()
        observer = self._create_observer_if_enabled(console=shared_console)
        
        # Add observer to the transfer subject and start the observer if available
        with ObserverContext(transfer, observer):
            try:
                # Send welcome message
                localhost = socket.gethostname()
                sock_name = sock.getsockname()
                transfer.send_message(sock, f"Hello from {localhost}! This is a file exchange service accessed via reverse tunnel. I am listening on port {sock_name[1]}")
                
                # Measure initial latency by timing a ping-pong message
                start_time = time.time()
                transfer.send_message(sock, "PING")
                pong = transfer.receive_message(sock)
                latency = (time.time() - start_time) / 2.0
                logger.info(f"Measured latency: {latency * 1000:.2f}ms")
                
                # Wait for command (but handle client ping first if it comes)
                command = transfer.receive_message(sock)
                if command == "CLIENT_PING":
                    transfer.send_message(sock, "PONG")
                    command = transfer.receive_message(sock)
                
                if not command:
                    logger.info("Client disconnected")
                    return
                    
                if command.startswith("SEND_FILE"):
                    self._handle_receive_file(transfer, sock, buffer_manager, latency)
                elif command.startswith("GET_FILE"):
                    _, file_name = command.split(":", 1)
                    self._handle_send_file(transfer, sock, buffer_manager, latency, file_name)
                else:
                    transfer.send_message(sock, f"Unknown command: {command}")
                    
            except Exception as e:
                logger.error(f"Error in file server handler: {e}")
                
    def _handle_receive_file(self, transfer: SocketTransferSubject, sock: socket.socket, 
                           buffer_manager: BufferManager, latency: float) -> None:
        """Handle receiving a file from client"""
        performance_config = self.config.get('performance', {})
        use_adaptive = performance_config.get("use_adaptive_transfer", True)
        logger.info(f"Client wants to send a file via reverse tunnel (using {'adaptive' if use_adaptive else 'standard'} buffering)")
        
        receiver_config = self.config.get('receiver', {})
        output_dir = receiver_config.get('output_dir', 'received_files')
        output_d = Path(output_dir).expanduser()
        output_d.mkdir(parents=True, exist_ok=True)
        
        if use_adaptive:
            buffer_manager.set_latency(latency)
            file_path = transfer.receive_file_adaptive(sock, output_d, buffer_manager)
            if not file_path:
                logger.warning("Adaptive receive failed, falling back to standard method")
                file_path = transfer.receive_file(sock, output_d)
        else:
            file_path = transfer.receive_file(sock, output_d)
        
        if file_path:
            transfer.send_message(sock, f"File received and saved as {file_path}")
            if use_adaptive:
                logger.info(f"Final buffer size used: {buffer_manager.get_buffer_size()/1024:.2f}KB")
                logger.info(f"Average transfer rate: {buffer_manager.get_average_transfer_rate()/1024/1024:.2f}MB/s")
        else:
            transfer.send_message(sock, "Failed to receive file")
            
    def _handle_send_file(self, transfer: SocketTransferSubject, sock: socket.socket,
                         buffer_manager: BufferManager, latency: float, file_name: str) -> None:
        """Handle sending a file to client"""
        performance_config = self.config.get('performance', {})
        use_adaptive = performance_config.get("use_adaptive_transfer", True)
        logger.info(f"Client requested file: {file_name} (using {'adaptive' if use_adaptive else 'standard'} buffering)")
        
        # Create a demo file if it doesn't exist
        file_to_send = Path(file_name).expanduser()
        if not file_to_send.exists():
            with open(file_to_send, 'w') as f:
                f.write(f"This is a test file '{file_name}' from the local machine (reverse tunnel).\n" * 10)
        
        if use_adaptive:
            buffer_manager.set_latency(latency)
            success = transfer.send_file_adaptive(sock, file_to_send, buffer_manager)
            if not success:
                logger.warning("Adaptive send failed, falling back to standard method")
                success = transfer.send_file(sock, file_to_send)
        else:
            success = transfer.send_file(sock, file_to_send)
            
        if success:
            logger.info(f"File {file_name} sent successfully")
            if use_adaptive:
                logger.info(f"Final buffer size used: {buffer_manager.get_buffer_size()/1024:.2f}KB")
                logger.info(f"Average transfer rate: {buffer_manager.get_average_transfer_rate()/1024/1024:.2f}MB/s")
        else:
            logger.error(f"Failed to send file {file_name}")
            
    def _run_server(self, port: int, mode: str = "message") -> None:
        """
        Run a server on the local machine that listens for connections coming through the reverse tunnel
        
        Args:
            port: Port to listen on
            mode: Server mode, either "message" or "file"
        """
        transfer = SocketTransferSubject()
        
        if mode == "file":
            handler = self._file_server_handler
            logger.info(f"Starting file server on local port {port}")
        else:
            handler = self._message_server_handler
            logger.info(f"Starting message server on local port {port}")
        
        transfer.run_server(port, handler)
        
    def _client_message_exchange(self, jump_server: str, remote_port: int) -> bool:
        """
        Execute message client operations connecting to the jump server's remote port
        
        Args:
            jump_server: Jump server hostname or IP
            remote_port: Remote port on jump server
        """
        transfer = SocketTransferSubject()
        logger.info(f"Message client connecting to {jump_server}:{remote_port}")
        
        sock = transfer.connect_to_server(jump_server, remote_port)
        if not sock:
            return False
        
        try:
            # Receive welcome message
            welcome = transfer.receive_message(sock)
            if welcome:
                logger.info(f"Server: {welcome}")
            
            # Send several test messages
            for i in range(3):
                test_message = f"Test message {i+1} from client"
                if not transfer.send_message(sock, test_message):
                    logger.error("Failed to send test message")
                    return False
                
                # Receive response
                response = transfer.receive_message(sock)
                if response:
                    logger.info(f"Server: {response}")
            
            # Send exit command
            transfer.send_message(sock, "exit")
            sock.close()
            return True
        except Exception as e:
            logger.error(f"Error in message exchange: {e}")
            return False
            
    def _client_file_exchange(self, 
        socket_server: str, 
        socket_port: int, 
        file_to_send: str = "", 
        file_to_get: str = ""
    ) -> bool:
        """
        Execute file client operations connecting to the jump server's remote port
        
        Args:
            socket_server: socket server hostname or IP
            socket_port: socket port on jump server
            file_to_send: Path to a file to send to the server (optional)
            file_to_get: Name of a file to get from the server (optional)
        """
        transfer = SocketTransferSubject()
        buffer_manager = BufferManager()
        observer = self._create_observer_if_enabled(console=shared_console)
        
        logger.info(f"File client connecting to {socket_server}:{socket_port}")
        
        sock = transfer.connect_to_server(socket_server, socket_port)
        if not sock:
            return False

        with ObserverContext(transfer, observer):
            try:
                # Receive welcome message
                welcome = transfer.receive_message(sock)
                if welcome:
                    logger.info(f"Server: {welcome}")
                
                # Handle latency measurement ping
                ping = transfer.receive_message(sock)
                if ping == "PING":
                    transfer.send_message(sock, "PONG")
                    logger.info("Responded to server latency measurement")
                
                # Measure latency from client side as well
                start_time = time.time()
                transfer.send_message(sock, "CLIENT_PING")
                response = transfer.receive_message(sock)
                latency = time.time() - start_time
                logger.info(f"Client measured latency: {latency * 1000:.2f}ms")
                
                performance_config = self.config.get('performance', {})
                use_adaptive = performance_config.get("use_adaptive_transfer", True)
                
                # Send a file if requested
                if file_to_send:
                    success = self._send_file_to_server(
                        transfer, sock, buffer_manager, latency, 
                        file_to_send, use_adaptive
                    )
                    if not success:
                        return False
                
                # Get a file if requested  
                elif file_to_get:
                    success = self._receive_file_from_server(
                        transfer, sock, buffer_manager, latency,
                        file_to_get, use_adaptive
                    )
                    if not success:
                        return False
                
                sock.close()
                return True
            except Exception as e:
                logger.error(f"Error in file exchange: {e}")
                return False
                
    def _send_file_to_server(self, transfer: SocketTransferSubject, sock: socket.socket,
                           buffer_manager: BufferManager, latency: float, 
                           file_to_send: str, use_adaptive: bool) -> bool:
        """Send a file to the server"""
        file_sent_path = Path(file_to_send).expanduser()
        
        if not file_sent_path.exists():
            logger.error(f"File to send does not exist: {file_sent_path}")
            return False
        
        # Tell server we want to send a file
        transfer.send_message(sock, "SEND_FILE")
        
        # Send the file using adaptive or standard method
        logger.info(f"Sending file {file_sent_path} using {'adaptive' if use_adaptive else 'standard'} buffering")
        if use_adaptive:
            buffer_manager.set_latency(latency)
            success = transfer.send_file_adaptive(sock, file_sent_path, buffer_manager)
            if not success:
                logger.warning("Adaptive send failed, falling back to standard method")
                success = transfer.send_file(sock, file_sent_path)
        else:
            success = transfer.send_file(sock, file_sent_path)
            
        if success:
            logger.info(f"File {file_sent_path} sent successfully")
            if use_adaptive:
                logger.info(f"Final buffer size used: {buffer_manager.get_buffer_size()/1024:.2f}KB")
                logger.info(f"Average transfer rate: {buffer_manager.get_average_transfer_rate()/1024/1024:.2f}MB/s")
            
            # Get server response
            response = transfer.receive_message(sock)
            if response:
                logger.info(f"Server: {response}")
        else:
            logger.error(f"Failed to send file {file_sent_path}")
            
        return success
        
    def _receive_file_from_server(self, transfer: SocketTransferSubject, sock: socket.socket,
                                buffer_manager: BufferManager, latency: float,
                                file_to_get: str, use_adaptive: bool) -> bool:
        """Receive a file from the server"""
        # Tell server we want to get a file
        transfer.send_message(sock, f"GET_FILE:{file_to_get}")
        
        # Receive the file using adaptive or standard method
        receiver_config = self.config.get('receiver', {})
        output_dir = receiver_config.get('output_dir', 'received_files')
        output_path = Path(output_dir).expanduser()
        output_path.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"Receiving file {file_to_get} using {'adaptive' if use_adaptive else 'standard'} buffering")
        if use_adaptive:
            buffer_manager.set_latency(latency)
            file_path = transfer.receive_file_adaptive(sock, output_path, buffer_manager)
            if not file_path:
                logger.warning("Adaptive receive failed, falling back to standard method")
                file_path = transfer.receive_file(sock, output_path)
        else:
            file_path = transfer.receive_file(sock, output_path)
            
        if file_path:
            logger.info(f"File received and saved as {file_path}")
            if use_adaptive:
                logger.info(f"Final buffer size used: {buffer_manager.get_buffer_size()/1024:.2f}KB")
                logger.info(f"Average transfer rate: {buffer_manager.get_average_transfer_rate()/1024/1024:.2f}MB/s")
        else:
            logger.error("Failed to receive file")
            
        return file_path is not None
        
    def run_as_receiver(self) -> None:
        """Run the application in receiver mode"""
        logger.info("Starting application in RECEIVER mode")
        
        transfer_config = self.config['transfer']
        local_port = transfer_config['local_port']
        remote_port = transfer_config['remote_port']
        mode = self.config.get('mode', 'file')
        
        # Start the server in a separate thread
        if self.config.get('start_server', True):
            self.server_thread = threading.Thread(
                target=self._run_server,
                args=(local_port, mode),
                daemon=True
            )
            self.server_thread.start()
            time.sleep(1)  # Give server time to start
        
        # Establish reverse tunnel
        logger.info(f"Establishing reverse tunnel:")
        logger.info(f"Local Machine: localhost:{local_port}")
        logger.info(f"Jump Server: {self.ssh_config.jump_server}:{remote_port}")
        
        self.reverse_tunnel = SSHTunnelReverse(
            ssh_config=self.ssh_config,
            remote_port=remote_port,
            local_host="localhost",
            local_port=local_port
        )
        
        if self.reverse_tunnel.establish_tunnel():
            logger.info("Reverse tunnel established successfully")
            logger.info("Receiver is ready to accept connections")
            
            try:
                logger.info("Tunnel is active. Press Ctrl+C to stop")
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                logger.info("Stopping receiver...")
        else:
            logger.error("Failed to establish tunnel")
            
    def run_as_sender(self) -> None:
        """Run the application in sender mode"""
        logger.info("Starting application in SENDER mode")
        
        transfer_config = self.config['transfer']
        local_port = transfer_config['local_port']
        remote_port = transfer_config['remote_port']
        mode = self.config.get('mode', 'file')
        
        # Establish reverse tunnel
        logger.info(f"Establishing reverse tunnel:")
        logger.info(f"Local Machine: localhost:{local_port}")
        logger.info(f"Jump Server: {self.ssh_config.jump_server}:{remote_port}")
        
        self.forward_tunnel = SSHTunnelForward(
            ssh_config=self.ssh_config,
            local_port=local_port,
            remote_host="localhost",
            remote_port=remote_port,
        )
        
        if self.forward_tunnel.establish_tunnel():
            logger.info("Forward tunnel established successfully")

            # Execute client operations if requested
            if self.config.get('start_client', True):
                time.sleep(2)  # Give tunnel time to stabilize
                logger.info("Executing sender operations through the tunnel...")
                
                if mode == "file":
                    sender_config = self.config.get('sender', {})
                    send_file = sender_config.get('file', '')
                    
                    if self._client_file_exchange(
                        socket_server="localhost",
                        socket_port=local_port,
                        file_to_send=send_file
                    ):
                        logger.info("File transfer successful")
                    else:
                        logger.error("File transfer failed")
                else:
                    if self._client_message_exchange(self.ssh_config.jump_server, remote_port):
                        logger.info("Message exchange successful")
                    else:
                        logger.error("Message exchange failed")
                        
            # Keep tunnel open for additional operations if needed
            try:
                logger.info("Sender operations completed. Press Ctrl+C to stop")
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                logger.info("Stopping sender...")
        else:
            logger.error("Failed to establish tunnel")
            
    def run(self) -> None:
        """Run the application based on configuration"""
        try:
            # Determine mode from configuration
            sender_enabled = self.config.get('sender', {}).get('enabled', False)
            receiver_enabled = self.config.get('receiver', {}).get('enabled', False)
            
            if sender_enabled and receiver_enabled:
                logger.error("Both sender and receiver modes are enabled. Please enable only one.")
                return
            elif sender_enabled:
                self.run_as_sender()
            elif receiver_enabled:
                self.run_as_receiver()
            else:
                logger.error("Neither sender nor receiver mode is enabled in configuration")
                return
                
        finally:
            self._cleanup()
            
    def _cleanup(self) -> None:
        """Clean up resources"""
        # Clean up shared Rich Progress Observer
        if (RICH_AVAILABLE and 
            self.config.get('progress', {}).get("use_progress_observer", False) and 
            self.config.get('progress', {}).get("use_rich_progress", True)):
            try:
                from core.rich_progress_observer import shutdown_shared_rich_observer
                shutdown_shared_rich_observer()
            except Exception as e:
                logger.error(f"Error shutting down shared observer: {e}")
        
        if self.server_thread and self.server_thread.is_alive():
            logger.info("Stopping server...")


def main():
    parser = argparse.ArgumentParser(description='File Transfer Application with Reverse SSH Tunnel')
    parser.add_argument('--config', required=True, 
                       help='Path to YAML configuration file (e.g., config_sender.yml or config_receiver.yml)')
    
    args = parser.parse_args()
    
    try:
        app = FileTransferApp(args.config)
        app.run()
    except Exception as e:
        logger.error(f"Application error: {e}")
        return 1
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
