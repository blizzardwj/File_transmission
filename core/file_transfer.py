import os
import socket
import time
import threading
import argparse
from typing import Optional
import pexpect
from core.utils import build_logger

from ssh_utils import (
    TransferMode,
    SSHConfig,
    BufferManager,
    NetworkMonitor,
    SSHTunnelForward,
    SSHTunnelReverse
)
from socket_data_transfer import SocketDataTransfer

# Configure logging
logger = build_logger(__name__)

class FileTransferBase:
    """Base class for file transfer operations with common functionality"""
    
    def __init__(self, mode: TransferMode, ssh_config: SSHConfig):
        self.mode = mode
        self.ssh_config = ssh_config
        self.buffer_manager = BufferManager()
        self.network_monitor = NetworkMonitor(ssh_config.jump_server, ssh_config)
        self.progress_thread = None
        self.stop_progress = False
        self.tunnel = None  # Will be initialized by subclasses
    
    def _display_progress(self, file_size: int, transferred: int):
        """Display progress bar for file transfer"""
        if file_size > 0:
            percent = min(100, int(transferred * 100 / file_size))
            bar_length = 30
            filled_length = int(bar_length * percent / 100)
            bar = '█' * filled_length + '-' * (bar_length - filled_length)
            
            print(f'\rProgress: |{bar}| {percent}% ({transferred}/{file_size} bytes)', end='')
            if percent == 100:
                print()
    
    def _progress_monitor(self, sock: socket.socket, file_size: int):
        """Thread to monitor and display transfer progress"""
        total_received = 0
        start_time = time.time()
        last_update = start_time
        last_bytes = 0
        
        while not self.stop_progress and total_received < file_size:
            # Try to get socket buffer status to estimate bytes transferred
            try:
                # This is platform specific and may not work on all systems
                if hasattr(socket, 'TIOCOUTQ'):
                    # For Linux systems
                    import fcntl
                    import array
                    buf = array.array('i', [0])
                    fcntl.ioctl(sock.fileno(), socket.TIOCOUTQ, buf)
                    total_received = file_size - buf[0]
                else:
                    # Fall back to estimation
                    current_time = time.time()
                    if current_time - last_update >= 1.0:
                        # Update rate once per second
                        total_received += int((current_time - last_update) * (total_received - last_bytes) / 
                                              (current_time - start_time))
                        last_update = current_time
                        last_bytes = total_received
            except (ImportError, OSError, AttributeError):
                # Fall back to simple time-based estimation
                current_time = time.time()
                elapsed = current_time - start_time
                if elapsed > 0:
                    # Simple linear estimation
                    total_received = min(file_size, int(file_size * elapsed / 
                                                     (elapsed + self.network_monitor.latency * 10)))
            
            self._display_progress(file_size, total_received)
            time.sleep(0.5)
        
        # Final update
        self._display_progress(file_size, file_size)

class FileSender(FileTransferBase):
    """Handles sending files through the jump server"""
    
    def __init__(self, ssh_config: SSHConfig, receiver_port: int = 9898):
        # receiver_port is the port that receiver is listening on
        super().__init__(TransferMode.SENDER, ssh_config)
        self.receiver_port = receiver_port
        self.local_port = 9191  # Local forwarding port
        self.tunnel = SSHTunnelForward(
            ssh_config, 
            self.local_port, 
            "localhost",    # Remote host itself 
            receiver_port   # Remote port on jump server
        )
    
    def send_file(self, file_path: str) -> bool:
        """
        Send a file to the target machine
        
        Args:
            file_path: Path to the file to send
            
        Returns:
            True if successful, False otherwise
        """
        if not os.path.exists(file_path):
            logger.error(f"File not found: {file_path}")
            return False
            
        file_size = os.path.getsize(file_path)
        file_name = os.path.basename(file_path)
        
        logger.info(f"Preparing to send file: {file_path} ({file_size} bytes)")
        
        # Establish tunnel first
        logger.info("Establishing forward SSH tunnel...")
        if not self.tunnel.establish_tunnel():
            logger.error("Failed to establish tunnel, aborting")
            return False
            
        # Get the local port
        local_port = self.tunnel.local_port
        if not local_port:
            logger.error("Failed to get local port")
            self.tunnel.close_tunnel()
            return False
            
        # Create socket for connection to tunnel
        sock = None
        try:
            logger.info(f"Connecting to tunnel at 127.0.0.1:{local_port}")
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect(('127.0.0.1', local_port))
            
            # Measure network conditions
            latency = self.network_monitor.measure_latency()
            logger.info(f"Measured latency: {latency * 1000:.2f}ms")
            
            # Adjust buffer size based on network conditions
            buffer_size = self.buffer_manager.adjust_buffer_size(1024 * 1024, latency)  # Start with 1MB/s estimate
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, buffer_size)
            
            # Create SocketDataTransfer with optimized buffer size
            transfer = SocketDataTransfer(buffer_size=buffer_size)
            
            # Use SocketDataTransfer to send the file
            success = transfer.send_file(sock, file_path)
            
            if success:
                logger.info(f"File {file_name} transferred successfully ({file_size} bytes)")
                return True
            else:
                logger.error("File transfer failed")
                return False
                
        except Exception as e:
            logger.error(f"Error sending file: {e}")
            return False
        finally:
            if sock:
                sock.close()
            # Close the tunnel when done
            logger.info("Closing ssh tunnel")
            self.tunnel.close_tunnel()
            
class FileReceiver(FileTransferBase):
    """Handles receiving files through the jump server"""
    
    def __init__(self, ssh_config: SSHConfig, listen_port: int = 9898):
        super().__init__(TransferMode.RECEIVER, ssh_config)
        self.listen_port = listen_port
        self.server_socket = None
        self.tunnel = SSHTunnelReverse(
            ssh_config,
            self.listen_port  # Remote port on jump server
        )
    
    def start_receiver(self, output_dir: str = '.') -> bool:
        """
        Start receiving files and save them to the specified directory
        
        Args:
            output_dir: Directory to save received files
            
        Returns:
            True if successful, False otherwise
        """
        if not os.path.exists(output_dir):
            try:
                os.makedirs(output_dir)
            except Exception as e:
                logger.error(f"Failed to create output directory: {e}")
                return False
        
        # Establish reverse tunnel first
        logger.info("Establishing reverse SSH tunnel...")
        if not self.tunnel.establish_tunnel():
            logger.error("Failed to establish reverse tunnel, aborting")
            return False
            
        # Add a delay to ensure tunnel is fully established
        time.sleep(2)
        
        # Create server socket
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        
        try:
            # Bind to all interfaces to ensure we can receive connections
            self.server_socket.bind(('0.0.0.0', self.listen_port))
            self.server_socket.listen(1)
            logger.info(f"Listening for incoming connections on port {self.listen_port}")
            
            while True:
                logger.info("Waiting for incoming connection...")
                client_sock, addr = self.server_socket.accept()
                logger.info(f"Connection from {addr}")
                
                # Handle client in a separate thread
                client_thread = threading.Thread(
                    target=self._handle_client,
                    args=(client_sock, output_dir)
                )
                client_thread.daemon = True
                client_thread.start()
                
        except KeyboardInterrupt:
            logger.info("Receiver stopped by user")
            return True
        except Exception as e:
            logger.error(f"Error in receiver: {e}")
            return False
        finally:
            if self.server_socket:
                logger.info("Closing server socket")
                self.server_socket.close()
            # Close the tunnel when done
            logger.info("Closing ssh reverse tunnel")
            self.tunnel.close_tunnel()
    
    def _handle_client(self, client_sock: socket.socket, output_dir: str):
        """Handle an individual client connection"""
        try:
            # Measure network conditions
            latency = self.network_monitor.measure_latency()
            logger.info(f"Measured latency: {latency * 1000:.2f}ms")
            
            # Adjust buffer size based on network conditions
            buffer_size = self.buffer_manager.adjust_buffer_size(1024 * 1024, latency)  # Start with 1MB/s estimate
            client_sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, buffer_size)
            
            # Create SocketDataTransfer with optimized buffer size
            transfer = SocketDataTransfer(buffer_size=buffer_size)
            
            # Start progress monitoring in a separate thread if needed
            self.stop_progress = False
            self.progress_thread = threading.Thread(
                target=self._progress_monitor, 
                args=(client_sock, 0)  # Initial file_size will be updated later
            )
            self.progress_thread.daemon = True
            self.progress_thread.start()
            
            # Receive the file using SocketDataTransfer
            output_path = transfer.receive_file(client_sock, output_dir)
            
            # Stop progress monitoring
            self.stop_progress = True
            if self.progress_thread:
                self.progress_thread.join(timeout=1.0)
                
            if output_path:
                logger.info(f"File received successfully: {output_path}")
            
            # Get final transfer statistics
            transfer_time = time.time() - start_time
            transfer_rate = file_size / transfer_time if transfer_time > 0 else 0
            
            if received_bytes == file_size:
                logger.info(f"File received successfully in {transfer_time:.2f} seconds ({transfer_rate/1024/1024:.2f} MB/s)")
            else:
                logger.warning(f"Incomplete file received: {received_bytes}/{file_size} bytes")
                
        except Exception as e:
            logger.error(f"Error handling client: {e}")
        finally:
            client_sock.close()

def main():
    parser = argparse.ArgumentParser(description='Secure file transfer via SSH jump server')
    parser.add_argument('--mode', choices=['send', 'receive'], required=True, 
                        help='Operation mode: send or receive files')
    parser.add_argument('--jump-server', required=True, 
                        help='Jump server hostname or IP address')
    parser.add_argument('--jump-user', required=True, 
                        help='Username for jump server')
    parser.add_argument('--jump-port', type=int, default=22, 
                        help='SSH port for jump server (default: 22)')
    parser.add_argument('--identity-file', 
                        help='SSH identity file (private key)')
    parser.add_argument('--use-password', action='store_true',
                        help='Use password authentication instead of key-based auth')
    parser.add_argument('--port', type=int, default=9898, 
                        help='Port to use for transfer (default: 9898)')
    
    subparsers = parser.add_subparsers(dest='command')
    
    # Sender specific arguments
    send_parser = subparsers.add_parser('send', help='Send file(s)')
    send_parser.add_argument('file', help='File to send')
    
    # Receiver specific arguments
    receive_parser = subparsers.add_parser('receive', help='Receive file(s)')
    receive_parser.add_argument('--output-dir', default='.', 
                               help='Directory to save received files (default: current directory)')
    
    args = parser.parse_args()
    
    # Create SSH configuration
    ssh_config = SSHConfig(
        jump_server=args.jump_server,
        jump_user=args.jump_user,
        jump_port=args.jump_port,
        identity_file=args.identity_file,
        use_password=args.use_password
    )
    
    # Import additional modules only if needed
    if args.use_password:
        import shutil  # For checking if sshpass is available
        try:
            import pexpect  # For interactive password handling
        except ImportError:
            logger.warning("pexpect module not available. Password handling may be limited.")
    
    if args.mode == 'send' or args.command == 'send':
        # Sender mode
        file_path = args.file
        if not os.path.isfile(file_path):
            logger.error(f"File not found: {file_path}")
            return
            
        sender = FileSender(ssh_config, args.port)
        sender.send_file(file_path)
        
    elif args.mode == 'receive' or args.command == 'receive':
        # Receiver mode
        output_dir = args.output_dir
        if not os.path.isdir(output_dir):
            logger.error(f"Output directory not found: {output_dir}")
            return
            
        receiver = FileReceiver(ssh_config, args.port)
        receiver.start_receiver(output_dir)

if __name__ == "__main__":
    main()
