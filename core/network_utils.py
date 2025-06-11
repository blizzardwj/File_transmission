import socket
import time
from typing import Optional, TYPE_CHECKING
import pexpect
import sys
from typing import cast
from core.utils import build_logger

# Import for type hints only
if TYPE_CHECKING:
    from .ssh_utils import SSHConfig

# Configure logging
logger = build_logger(__name__)

class BufferManager:
    """Manages the buffer size for optimal transfer speed with adaptive adjustment"""
    
    # Default buffer sizes in bytes
    DEFAULT_BUFFER_SIZE = 64 * 1024  # 64KB
    MINIMUM_BUFFER_SIZE = 8 * 1024  # 8KB minimum buffer size
    MAXIMUM_BUFFER_SIZE = 1 * 1024 * 1024  # 1MB maximum buffer size

    def __init__(self, 
        initial_size: int = DEFAULT_BUFFER_SIZE,
        max_size: int = MAXIMUM_BUFFER_SIZE,
        latency: float = 0.1
    ):
        self.buffer_size = initial_size
        self.max_size = max_size
        self.min_size = self.MINIMUM_BUFFER_SIZE
        self.latency = latency  # Network latency in seconds
        self.transfer_history = []  # Store transfer performance history
        self.adjustment_factor = 0.2  # 20% adjustment per iteration
        
    def measure_initial_bandwidth(self, sock: socket.socket) -> float:
        """
        Measure initial bandwidth by sending test data
        
        Args:
            sock: Socket to test on
            
        Returns:
            Estimated bandwidth in bytes/second
        """
        test_data = b'0' * (32 * 1024)  # 32KB test data
        start_time = time.time()
        
        try:
            sock.sendall(test_data)
            end_time = time.time()
            
            transfer_time = end_time - start_time
            if transfer_time > 0:
                bandwidth = len(test_data) / transfer_time
                logger.info(f"Measured initial bandwidth: {bandwidth / 1024 / 1024:.2f} MB/s")
                return bandwidth
        except Exception as e:
            logger.warning(f"Failed to measure initial bandwidth: {e}")
            
        return 1024 * 1024  # 1MB/s default
        
    def adjust_buffer_size(self, transfer_rate: float) -> int:
        """
        Dynamically adjust buffer size based on network conditions
        
        Args:
            transfer_rate: Current transfer rate in bytes/second
            
        Returns:
            New buffer size in bytes
        """
        # BDP (Bandwidth-Delay Product) calculation using internal latency
        optimal_size = int(transfer_rate * self.latency)
        
        # Apply constraints to keep the buffer size reasonable
        min_size = self.min_size  # 8KB minimum
        max_size = self.max_size  # 1MB maximum

        self.buffer_size = max(min_size, min(optimal_size, max_size))
        logger.info(f"Buffer size adjusted to: {self.buffer_size / 1024:.2f}KB")
        return self.buffer_size
    
    def adaptive_adjust(self, bytes_transferred: int, transfer_time: float) -> int:
        """
        Adaptively adjust buffer size based on actual transfer performance
        
        Args:
            bytes_transferred: Number of bytes transferred
            transfer_time: Time taken for transfer in seconds
            
        Returns:
            New buffer size in bytes
        """
        if transfer_time <= 0:
            return self.buffer_size
            
        # Calculate actual transfer rate
        actual_rate = bytes_transferred / transfer_time
        
        # Store transfer performance
        self.transfer_history.append({
            'rate': actual_rate,
            'time': transfer_time,
            'bytes': bytes_transferred
        })
        
        # Keep only recent history (last 10 transfers)
        if len(self.transfer_history) > 10:
            self.transfer_history.pop(0)
        
        # Calculate optimal size based on BDP using internal latency
        optimal_size = int(actual_rate * self.latency)
        
        # Gradual adjustment to avoid oscillation
        new_size = int(self.buffer_size * (1 - self.adjustment_factor) + 
                      optimal_size * self.adjustment_factor)
        
        # Apply constraints
        min_size = self.min_size  # 8KB minimum
        max_size = self.max_size  # 1MB maximum

        self.buffer_size = max(min_size, min(new_size, max_size))
        
        logger.debug(f"Adaptive buffer adjustment: {actual_rate/1024/1024:.2f} MB/s -> {self.buffer_size/1024:.2f}KB buffer")
        return self.buffer_size
    
    def get_buffer_size(self) -> int:
        """Get current buffer size"""
        return self.buffer_size
    
    def get_average_transfer_rate(self) -> float:
        """Get average transfer rate from recent history"""
        if not self.transfer_history:
            return 1024 * 1024  # 1MB/s default

        total_bytes = sum(h['bytes'] for h in self.transfer_history)
        total_time = sum(h['time'] for h in self.transfer_history)
        
        if total_time > 0:
            return total_bytes / total_time
        return 1024 * 1024

    def set_latency(self, latency: float) -> None:
        """Update the network latency estimate"""
        self.latency = latency
        logger.debug(f"Network latency updated to: {self.latency:.4f}s")

class NetworkMonitor:
    """Monitors network conditions to optimize transfer"""
    
    def __init__(self, target_host: str, ssh_config: Optional['SSHConfig'] = None):
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
