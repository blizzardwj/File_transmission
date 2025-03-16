import os
import socket
import subprocess
import time
import threading
import logging
import argparse
import getpass
from enum import Enum
from dataclasses import dataclass
from typing import Optional, Tuple, List
import shutil
import pexpect

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('file_transfer')

class TransferMode(Enum):
    SENDER = "sender"
    RECEIVER = "receiver"

@dataclass
class SSHConfig:
    jump_server: str
    jump_user: str
    jump_port: int = 22
    identity_file: Optional[str] = None # SSH identity file (private key)
    use_password: bool = False # Default to key-based authentication
    password: Optional[str] = None # Store password if provided
    
    def get_ssh_command_base(self) -> List[str]:
        """Returns base SSH command with common options"""
        cmd = ["ssh"]
        if self.identity_file:
            cmd.extend(["-i", self.identity_file])
            cmd.extend(["-o", "PreferredAuthentications=publickey"])
        elif self.use_password:
            # Force password authentication
            cmd.extend(["-o", "PreferredAuthentications=password"])
            cmd.extend(["-o", "PubkeyAuthentication=no"])
        cmd.extend(["-p", str(self.jump_port)])
        return cmd

class BufferManager:
    """Manages the buffer size in jump server for optimal transfer speed"""
    
    # Default buffer sizes in bytes
    DEFAULT_BUFFER_SIZE = 64 * 1024  # 64KB
    
    def __init__(self, initial_size: int = DEFAULT_BUFFER_SIZE):
        self.buffer_size = initial_size
        
    def adjust_buffer_size(self, transfer_rate: float, latency: float) -> int:
        """
        Dynamically adjust buffer size based on network conditions
        
        Args:
            transfer_rate: Current transfer rate in bytes/second
            latency: Network latency in seconds
            
        Returns:
            New buffer size in bytes
        """
        # BDP (Bandwidth-Delay Product) calculation
        optimal_size = int(transfer_rate * latency)
        
        # Apply constraints to keep the buffer size reasonable
        min_size = 8 * 1024  # 8KB minimum
        max_size = 8 * 1024 * 1024  # 8MB maximum
        
        self.buffer_size = max(min_size, min(optimal_size, max_size))
        logger.info(f"Buffer size adjusted to: {self.buffer_size / 1024:.2f}KB")
        return self.buffer_size
    
    def get_buffer_size(self) -> int:
        """Get current buffer size"""
        return self.buffer_size

class NetworkMonitor:
    """Monitors network conditions to optimize transfer"""
    
    def __init__(self, target_host: str, ssh_config: SSHConfig = None):
        self.target_host = target_host
        self.ssh_config = ssh_config
        self.latency = 0.1  # Initial default latency estimate (100ms)
        
    def measure_latency(self) -> float:
        """
        Measure network latency to the target host through jump server
        
        Returns:
            Latency in seconds
        """
        if self.ssh_config:
            # Measure latency through jump server
            cmd = self.ssh_config.get_ssh_command_base()
            cmd.append(f"{self.ssh_config.jump_user}@{self.ssh_config.jump_server}")
            cmd.extend(["ping", "-c", "3", self.target_host])
            
            try:
                output = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True)
                if "time=" in output:
                    # Extract average time from ping output
                    latency_lines = [line for line in output.split('\n') if "time=" in line]
                    if latency_lines:
                        latency_values = []
                        for line in latency_lines:
                            try:
                                time_part = line.split("time=")[1].split()[0]
                                latency_values.append(float(time_part.replace("ms", "")))
                            except (IndexError, ValueError):
                                continue
                        
                        if latency_values:
                            avg_latency = sum(latency_values) / len(latency_values)
                            self.latency = avg_latency / 1000  # Convert from ms to seconds
            except (subprocess.SubprocessError, Exception) as e:
                logger.warning(f"Failed to measure latency: {e}")
                # Fall back to default or previous value
        
        return self.latency
    
    def estimate_bandwidth(self, data_size: int, transfer_time: float) -> float:
        """
        Estimate bandwidth based on actual transfer metrics
        
        Args:
            data_size: Size of transferred data in bytes
            transfer_time: Time taken to transfer in seconds
            
        Returns:
            Estimated bandwidth in bytes/second
        """
        if transfer_time > 0:
            return data_size / transfer_time
        return 1024 * 1024  # Default 1MB/s if can't calculate

class SSHTunnelBase:
    """Base class for managing SSH tunnel creation and maintenance"""
    
    def __init__(self, ssh_config: SSHConfig):
        self.ssh_config = ssh_config
        self.tunnel_process = None
        self.is_active = False
        
    def _establish_tunnel_common(self, cmd: List[str]) -> bool:
        """
        Common tunnel establishment logic
        
        Args:
            cmd: SSH command with arguments list
            
        Returns:
            True if successful, False otherwise
        """
        try:
            logger.info(f"Establishing SSH tunnel: {' '.join(cmd)}")
            
            # Initialize is_pexpect flag
            self.is_pexpect = False
            
            if self.ssh_config.use_password:
                # Get password interactively if not already provided
                if not self.ssh_config.password:
                    password = getpass.getpass(f"Enter SSH password for {self.ssh_config.jump_user}@{self.ssh_config.jump_server}: ")
                    self.ssh_config.password = password
                
                # For password auth with sshpass
                if shutil.which("sshpass"):
                    # Use sshpass for non-interactive password input
                    sshpass_cmd = ["sshpass", "-p", self.ssh_config.password]
                    cmd = sshpass_cmd + cmd
                    self.tunnel_process = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE
                    )
                    # Use subprocess-specific check
                    self.is_pexpect = False
                else:
                    # Fallback to expect-like behavior if sshpass is not available
                    try:
                        # Convert list to command string
                        cmd_str = " ".join(cmd)
                        self.tunnel_process = pexpect.spawn(cmd_str)
                        # Look for password prompt
                        index = self.tunnel_process.expect(['password:', pexpect.EOF, pexpect.TIMEOUT], timeout=10)
                        if index == 0:  # Password prompt found
                            self.tunnel_process.sendline(self.ssh_config.password)
                        elif index == 1:  # EOF
                            logger.error("SSH process ended unexpectedly")
                            return False
                        elif index == 2:  # TIMEOUT
                            logger.error("SSH process timed out waiting for password prompt")
                            return False
                        # Flag that we're using pexpect
                        self.is_pexpect = True
                    except ImportError:
                        # If pexpect is not available, fall back to basic approach
                        logger.warning("Neither sshpass nor pexpect available. Password authentication may fail.")
                        self.tunnel_process = subprocess.Popen(
                            cmd,
                            stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE
                        )
                        # Try to send password
                        try:
                            self.tunnel_process.communicate(input=self.ssh_config.password.encode() + b'\n', timeout=5)
                        except subprocess.TimeoutExpired:
                            # This is expected - means ssh is running and waiting for connection
                            pass
                        # Use subprocess-specific check
                        self.is_pexpect = False
            else:
                # For key-based auth, proceed as normal
                self.tunnel_process = subprocess.Popen(
                    cmd, 
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
                # Use subprocess-specific check
                self.is_pexpect = False
            
            # Wait for tunnel to establish
            time.sleep(2)
            
            # Check if process is still running - different methods for pexpect vs subprocess
            if self.is_pexpect:
                # For pexpect objects, check if the process is alive
                if self.tunnel_process.isalive():
                    self.is_active = True
                    logger.info("SSH tunnel established successfully")
                    return True
                else:
                    logger.error(f"Failed to establish SSH tunnel with pexpect")
                    return False
            else:
                # For subprocess objects, use poll()
                if self.tunnel_process.poll() is None:
                    self.is_active = True
                    logger.info("SSH tunnel established successfully")
                    return True
                else:
                    stdout, stderr = self.tunnel_process.communicate()
                    logger.error(f"Failed to establish SSH tunnel: {stderr.decode()}")
                    return False
                
        except Exception as e:
            logger.error(f"Error establishing SSH tunnel: {e}")
            return False
            
    def close_tunnel(self):
        """Close the SSH tunnel"""
        if self.tunnel_process and self.is_active:
            logger.info("Closing SSH tunnel")
            
            # Different handling based on process type
            if hasattr(self, 'is_pexpect') and self.is_pexpect:
                # For pexpect objects
                try:
                    self.tunnel_process.close(force=True)  # Force kill the process
                except Exception as e:
                    logger.warning(f"Error closing pexpect tunnel: {e}")
            else:
                # For subprocess objects
                try:
                    self.tunnel_process.terminate()
                    self.tunnel_process.wait(timeout=5)
                except Exception as e:
                    logger.warning(f"Error closing subprocess tunnel: {e}")
                    # Force kill if terminate fails
                    try:
                        self.tunnel_process.kill()
                    except:
                        pass
                        
            self.is_active = False
            
class SSHTunnelForward(SSHTunnelBase):
    """Manages forward SSH tunnel creation and maintenance"""
    
    def __init__(self, ssh_config: SSHConfig, local_port: int, remote_host: str, remote_port: int):
        super().__init__(ssh_config)
        self.local_port = local_port
        self.remote_host = remote_host
        self.remote_port = remote_port
        
    def establish_tunnel(self) -> bool:
        """
        Create forward SSH tunnel through the jump server
        
        Returns:
            True if successful, False otherwise
        """
        cmd = self.ssh_config.get_ssh_command_base()
        
        # Add port forwarding
        cmd.extend([
            "-L", f"{self.local_port}:{self.remote_host}:{self.remote_port}",
            "-N",  # Don't execute a remote command
            f"{self.ssh_config.jump_user}@{self.ssh_config.jump_server}"
        ])
        
        return self._establish_tunnel_common(cmd)

class SSHTunnelReverse(SSHTunnelBase):
    """Manages reverse SSH tunnel creation and maintenance"""
    
    def __init__(self, ssh_config: SSHConfig, remote_port: int, local_host: str = "localhost", local_port: int = None):
        super().__init__(ssh_config)
        self.remote_port = remote_port
        self.local_host = local_host
        self.local_port = local_port if local_port else remote_port
        
    def establish_tunnel(self) -> bool:
        """
        Create reverse SSH tunnel through the jump server
        
        Returns:
            True if successful, False otherwise
        """
        cmd = self.ssh_config.get_ssh_command_base()
        
        # Add reverse port forwarding
        cmd.extend([
            "-R", f"{self.remote_port}:{self.local_host}:{self.local_port}",
            "-N",  # Don't execute a remote command
            f"{self.ssh_config.jump_user}@{self.ssh_config.jump_server}"
        ])
        
        return self._establish_tunnel_common(cmd)

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
        Send a file to the receiver through the jump server
        
        Args:
            file_path: Path to the file to send
            
        Returns:
            True if successful, False otherwise
        """
        if not os.path.exists(file_path):
            logger.error(f"File not found: {file_path}")
            return False
        
        file_size = os.path.getsize(file_path)
        if file_size == 0:
            logger.error("File is empty")
            return False
        
        logger.info(f"Preparing to send file: {file_path} ({file_size} bytes)")
        
        # Establish tunnel
        if not self.tunnel.establish_tunnel():
            return False
        
        # Add a delay to ensure tunnel is fully established
        time.sleep(2)
        
        sock = None
        try:
            # Measure network conditions
            latency = self.network_monitor.measure_latency()
            logger.info(f"Measured latency: {latency * 1000:.2f}ms")
            
            # Adjust buffer size based on network conditions
            # Start with an estimated bandwidth of 1MB/s
            init_bandwidth = 1024 * 1024
            buffer_size = self.buffer_manager.adjust_buffer_size(init_bandwidth, latency)
            
            # Connect to forwarded port
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, buffer_size)
            
            # Add socket timeout to prevent hanging
            sock.settimeout(10)
            
            logger.info(f"Connecting to localhost:{self.local_port} (forwarded to localhost:{self.receiver_port})")
            try:
                sock.connect(('localhost', self.local_port))
                logger.info("Connection established successfully")
            except ConnectionRefusedError:
                logger.error(f"Connection refused. Make sure the receiver is running and listening on port {self.receiver_port}")
                return False
            except Exception as e:
                logger.error(f"Connection error: {e}")
                return False
            
            # Reset timeout for data transfer
            sock.settimeout(None)
            
            # Send file metadata
            file_name = os.path.basename(file_path)
            metadata = f"{file_name}|{file_size}".encode()
            logger.info(f"Sending metadata: {metadata.decode()}")
            sock.send(f"{len(metadata):10d}".encode())
            sock.send(metadata)
            
            # Wait for acknowledgment from receiver
            try:
                sock.settimeout(10)
                ack = sock.recv(2)
                if ack != b'OK':
                    logger.error(f"Did not receive proper acknowledgment from receiver. Got: {ack}")
                    return False
                logger.info("Received acknowledgment from receiver")
                sock.settimeout(None)
            except socket.timeout:
                logger.error("Timed out waiting for acknowledgment from receiver")
                return False
            except Exception as e:
                logger.error(f"Error receiving acknowledgment: {e}")
                return False
            
            # Start progress monitoring in a separate thread
            self.stop_progress = False
            self.progress_thread = threading.Thread(
                target=self._progress_monitor,
                args=(sock, file_size)
            )
            self.progress_thread.start()
            
            # Send file data
            start_time = time.time()
            sent_bytes = 0
            
            with open(file_path, 'rb') as f:
                while True:
                    data = f.read(buffer_size)
                    if not data:
                        break
                    
                    sock.sendall(data)
                    sent_bytes += len(data)
                    
                    # Periodically adjust buffer size based on actual transfer rate
                    if sent_bytes % (buffer_size * 10) == 0:
                        elapsed = time.time() - start_time
                        if elapsed > 0:
                            transfer_rate = sent_bytes / elapsed
                            buffer_size = self.buffer_manager.adjust_buffer_size(transfer_rate, latency)
                            # Update socket buffer size
                            sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, buffer_size)
            
            # Get final transfer statistics
            transfer_time = time.time() - start_time
            transfer_rate = file_size / transfer_time if transfer_time > 0 else 0
            
            # Stop progress monitoring
            self.stop_progress = True
            if self.progress_thread:
                self.progress_thread.join()
            
            logger.info(f"File sent successfully in {transfer_time:.2f} seconds ({transfer_rate/1024/1024:.2f} MB/s)")
            return True
            
        except Exception as e:
            logger.error(f"Error sending file: {e}")
            return False
        finally:
            if sock:
                logger.info("Closing socket connection")
                sock.close()
            logger.info("Closing SSH tunnel")
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
            
            # Adjust buffer size
            buffer_size = self.buffer_manager.adjust_buffer_size(1024 * 1024, latency)  # Start with 1MB/s estimate
            client_sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, buffer_size)
            
            # Receive metadata size (fixed 10 bytes)
            logger.info("Waiting to receive metadata size...")
            metadata_size_bytes = client_sock.recv(10)
            if not metadata_size_bytes:
                logger.error("Failed to receive metadata size")
                return
            
            try:
                metadata_size = int(metadata_size_bytes.decode().strip())
                logger.info(f"Metadata size: {metadata_size} bytes")
            except ValueError:
                logger.error(f"Invalid metadata size received: {metadata_size_bytes}")
                return
            
            # Receive metadata
            metadata_bytes = client_sock.recv(metadata_size)
            if not metadata_bytes:
                logger.error("Failed to receive metadata")
                return
            
            try:
                metadata = metadata_bytes.decode()
                file_name, file_size_str = metadata.split('|')
                file_size = int(file_size_str)
                logger.info(f"Receiving file: {file_name} ({file_size} bytes)")
            except (ValueError, IndexError) as e:
                logger.error(f"Invalid metadata format: {e}")
                return
            
            # Send acknowledgment to sender
            logger.info("Sending acknowledgment to sender")
            client_sock.send(b'OK')
            
            # Prepare output file
            output_path = os.path.join(output_dir, file_name)
            
            # Start progress monitoring
            self.stop_progress = False
            self.progress_thread = threading.Thread(
                target=self._progress_monitor,
                args=(client_sock, file_size)
            )
            self.progress_thread.start()
            
            # Receive file data
            start_time = time.time()
            received_bytes = 0
            
            with open(output_path, 'wb') as f:
                while received_bytes < file_size:
                    # Calculate remaining bytes
                    remaining = file_size - received_bytes
                    bytes_to_read = min(buffer_size, remaining)
                    
                    data = client_sock.recv(bytes_to_read)
                    if not data:
                        break
                    
                    f.write(data)
                    received_bytes += len(data)
                    
                    # Periodically adjust buffer size based on actual transfer rate
                    if received_bytes % (buffer_size * 10) == 0:
                        elapsed = time.time() - start_time
                        if elapsed > 0:
                            transfer_rate = received_bytes / elapsed
                            buffer_size = self.buffer_manager.adjust_buffer_size(transfer_rate, latency)
                            # Update socket buffer size
                            client_sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, buffer_size)
            
            # Stop progress monitoring
            self.stop_progress = True
            if self.progress_thread:
                self.progress_thread.join()
            
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
