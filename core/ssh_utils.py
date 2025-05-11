import socket
import subprocess
import time
import logging
import getpass
import shutil
from enum import Enum
from dataclasses import dataclass
from typing import Optional, List
import pexpect
from core.utils import build_logger

# Configure logging
logger = build_logger(__name__)

class TransferMode(Enum):
    SENDER = "sender"
    RECEIVER = "receiver"

@dataclass
class SSHConfig:
    jump_server: str
    jump_user: str
    jump_port: int = 22
    identity_file: Optional[str] = None  # SSH identity file (private key)
    use_password: bool = False  # Default to key-based authentication
    password: Optional[str] = None  # Store password if provided
    
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

    def measure_latency_with_ssh(self) -> float:
        """Measure network latency using SSH connection timing"""
        if not self.ssh_config:
            logger.warning("Cannot measure latency with SSH: No SSH configuration provided")
            return self.latency
            
        try:
            start_time = time.time()
            
            # Construct SSH command to just return immediately
            cmd = self.ssh_config.get_ssh_command_base()
            cmd.extend([
                "-o", "ConnectTimeout=5",
                f"{self.ssh_config.jump_user}@{self.ssh_config.jump_server}",
                "echo", "connected"
            ])
            
            # Convert to string for pexpect
            cmd_str = " ".join(cmd)
            child = pexpect.spawn(cmd_str, timeout=10)
            
            # Handle password if needed
            if self.ssh_config.use_password and self.ssh_config.password:
                child.expect('password:')
                child.sendline(self.ssh_config.password)
                
            # Wait for command completion
            child.expect(pexpect.EOF)
            end_time = time.time()
            
            # Calculate latency
            self.latency = (end_time - start_time)
            logger.info(f"SSH latency: {self.latency:.4f}s")
            return self.latency
            
        except Exception as e:
            logger.warning(f"Failed to measure latency with SSH: {e}")
            return self.latency

    def measure_latency_with_socket(self) -> float:
        """Measure network latency using TCP socket connection"""
        try:
            # Default port to try connecting to
            port = 22  # SSH port is often open
            num_attempts = 3
            latency_values = []
            
            for _ in range(num_attempts):
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(5)
                
                start_time = time.time()
                try:
                    sock.connect((self.target_host, port))
                    end_time = time.time()
                    latency_values.append(end_time - start_time)
                except (socket.timeout, socket.error) as e:
                    logger.debug(f"Socket connection attempt failed: {e}")
                finally:
                    sock.close()
                    
            if latency_values:
                self.latency = sum(latency_values) / len(latency_values)
                logger.info(f"Socket latency: {self.latency:.4f}s")
                
            return self.latency
            
        except Exception as e:
            logger.warning(f"Failed to measure latency with socket: {e}")
            return self.latency

    def measure_latency(self) -> float:
        """
        Measure network latency to the target host using direct ping
        
        Returns:
            Latency in seconds
        """
        if 0: #self.ssh_config:
            # May need to input password so it will affect the latency
            return self.measure_latency_with_ssh()
        else:
            return self.measure_latency_with_socket()
    
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
                
                # Check for sshpass availability
                sshpass_available = shutil.which("sshpass")
                
                # For password auth with sshpass
                if sshpass_available:
                    logger.info("Found sshpass: using for non-interactive password authentication")
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
                    logger.warning("sshpass not found in system PATH. Install it for better password handling, or falling back to pexpect.")
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
                        # If pexpect is not available, log error and return failure
                        logger.error("Cannot establish SSH connection: both sshpass and pexpect are unavailable")
                        logger.error("Please install sshpass (e.g., 'sudo apt-get install sshpass' on Debian/Ubuntu) or pexpect (pip install pexpect)")
                        return False
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
