#!/usr/bin/env python3
"""
Reverse SSH Tunnel Experiment with Progress Observer Integration

This script demonstrates establishing a reverse SSH tunnel between two devices:
1. Local Machine: Where this script runs
    Act as a real socket server (Server).
    And simulate a file sender (Sender).
    Actually, there are two objects on the local machine.
    
2. Jump Server: Remote SSH server that accepts the reverse tunnel
    Act as a proxy server (Proxy) that forwards the traffic to the real socket server.
    And receive the file from the sender.

TWO TASKS:
1. Send messages between the local machine and the jump server through the reverse tunnel.
2. Send files from a simulated client (on the local machine) to the jump server (actually to the local machine's socket server) through the reverse tunnel.

In a reverse tunnel, a port on the remote jump server is forwarded to a local port on your machine.
This allows external clients to connect to the jump server's port and have their traffic forwarded to 
your local machine, which is useful when your machine is behind a firewall or NAT.

PROGRESS OBSERVER INTEGRATION:
This script now includes observer pattern integration for file transfer progress tracking:
- ObserverContext: Context manager for automatic observer lifecycle management
- create_observer_if_enabled(): Factory function for creating observers based on configuration
- Integrated into file_server_handler() and simulate_client_file_exchange() functions
- Configurable via DEBUG_CONFIG["use_progress_observer"] and DEBUG_CONFIG["use_rich_progress"]
- Supports Rich progress bars (if available) or fallback to simple text output
- Ensures proper cleanup of observers even when exceptions occur

OBSERVER MANAGEMENT APPROACHES:
1. Context Manager Pattern (implemented): Uses 'with ObserverContext(transfer, observer):' 
   - Automatically adds observer on entry, removes on exit
   - Guarantees cleanup even with exceptions
   - Clear scope definition for observer usage

2. Alternative approaches considered but not implemented:
   - Decorator Pattern: @with_observer decorator for handler functions
   - Class-based: ObservableTransfer class extending SocketTransferSubject

CONFIGURATION REQUIREMENTS:
The jumper server (remote host) should let the remote port (it own port) be accessible from the external network. Here are the steps:
1. When establishing reverse tunnel, "0.0.0.0" should be set as remote_host (like remote_host:remote_port:localhost:local_port).  
2. Configure GatewayPorts for SSH daemon on the jump server to allow ssh connections from external clients to the remote port. Configure `GatewayPorts clientspecified` or `GatewayPorts yes` in the /etc/ssh/sshd_config file.
3. Restart sshd service on the jump server. `systemctl restart ssh.service` and `systemctl restart ssh.socket`.

TUNNEL MECHANISM:
When a reverse SSH tunnel is established, the remote port on the jump server automatically becomes a listening port that can accept client connections, even without explicitly starting a socket server on the jump server. The SSH daemon itself handles the port binding and forwards all incoming connections through the tunnel to the local machine.

Data Flow:
External Client -> Jump Server:remote_port (SSH daemon listening) -> Resverse SSH Tunnel -> Local Machine:local_port (your socket server)

SERVER or CLIENT PERSPECTIVE:

Socket communication:
Localhost port is listening. Consider it as a socket server.

Reverse tunnel transport:
Remotehost port is listening. Consider it as a proxy server.

Usage:
    python reverse_ssh_tunnel.py --jump-server <server> --jump-user <user> [options]

Example:
    python reverse_ssh_tunnel.py --jump-server jumphost.example.com --jump-user admin --remote-port 8022
"""

import os
import sys
import time
from pathlib import Path
import socket
import logging
import argparse
import threading

# Add parent directory to path to import ssh_utils
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.ssh_utils import SSHConfig, SSHTunnelReverse, BufferManager
from core.socket_transfer_subject import SocketTransferSubject

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('reverse_tunnel_experiment')

# Import observer-related modules
try:
    from core.rich_progress_observer import create_progress_observer, RichProgressObserver
    from rich.progress import Progress
    from rich.console import Console
    RICH_AVAILABLE = True
except ImportError as e:
    logger.warning(f"Rich not available ({e}), using fallback observer")
    from core.rich_progress_observer import create_progress_observer
    RICH_AVAILABLE = False


class ObserverContext:
    """
    Context manager for automatic observer management
    
    Ensures that observers are properly added and removed from SocketTransferSubject instances,
    and manages the observer's lifecycle (start/stop) if the observer supports it.
    This ensures proper cleanup even if exceptions occur during the transfer operations.
    """
    
    def __init__(self, subject: SocketTransferSubject, observer):
        """
        Initialize the observer context
        
        Args:
            subject: SocketTransferSubject instance to manage
            observer: Observer instance implementing IProgressObserver interface
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
                    logger.debug(f"Observer {self.observer.__class__.__name__} stopped")
                except Exception as e:
                    logger.warning(f"Failed to stop observer {self.observer.__class__.__name__}: {e}")
        
        return False  # Don't suppress exceptions


def create_observer_if_enabled(console=None):
    """
    Create a progress observer if enabled in DEBUG_CONFIG
    
    Args:
        console: Rich Console 实例（可选）
        
    Returns:
        IProgressObserver instance or None if disabled
    """
    if not DEBUG_CONFIG.get("use_progress_observer", False):
        return None
    
    try:
        use_rich = DEBUG_CONFIG.get("use_rich_progress", True) and RICH_AVAILABLE
        # 默认请求共享观察者以避免进度条重叠
        return create_progress_observer(use_rich=use_rich, shared_mode=True, console=console)
    except Exception as e:
        logger.warning(f"Failed to create progress observer: {e}")
        return None


def message_server_handler(sock: socket.socket) -> None:
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

def file_server_handler(sock: socket.socket) -> None:
    """
    Handle a client connection for file exchange server
    This runs on the local machine and receives connections via the jump server
    """
    transfer = SocketTransferSubject()
    buffer_manager = BufferManager()
    observer = create_observer_if_enabled()
    
    # Add observer to the transfer subject and start the observer if available
    with ObserverContext(transfer, observer):
        try:
            # Send welcome message, give the name or ip of localhost
            localhost = socket.gethostname()
            sock_name = sock.getsockname()
            transfer.send_message(sock, f"Hello from the {localhost}! This is a file exchange service accessed via reverse tunnel. I am listening on port {sock_name[1]}")
            
            # Measure initial latency by timing a ping-pong message
            start_time = time.time()
            transfer.send_message(sock, "PING")
            pong = transfer.receive_message(sock)
            latency = (time.time() - start_time) / 2.0  # Round trip time / 2
            logger.info(f"Measured latency: {latency * 1000:.2f}ms")
            
            # Wait for command (but handle client ping first if it comes)
            command = transfer.receive_message(sock)
            if command == "CLIENT_PING":
                # Respond to client ping and get actual command
                transfer.send_message(sock, "PONG")
                command = transfer.receive_message(sock)
            
            if not command:
                logger.info("Client disconnected")
                return
                
            if command.startswith("SEND_FILE"):
                # Receive file using adaptive or standard method based on configuration
                use_adaptive = DEBUG_CONFIG.get("use_adaptive_transfer", True)
                logger.info(f"Client wants to send a file via reverse tunnel (using {'adaptive' if use_adaptive else 'standard'} buffering)")
                output_dir = DEBUG_CONFIG.get("received_files_dir", "received_files")
                output_d = Path(output_dir).expanduser()
                output_d.mkdir(parents=True, exist_ok=True)
                
                if use_adaptive:
                    # Try adaptive receive first, fallback to standard method
                    file_path = transfer.receive_file_adaptive(sock, output_d, buffer_manager, latency)
                    if not file_path:
                        logger.warning("Adaptive receive failed, falling back to standard method")
                        file_path = transfer.receive_file(sock, output_d)
                else:
                    # Use standard method directly
                    file_path = transfer.receive_file(sock, output_d)
                
                if file_path:
                    transfer.send_message(sock, f"File received and saved as {file_path}")
                    if use_adaptive:
                        logger.info(f"Final buffer size used: {buffer_manager.get_buffer_size()/1024:.2f}KB")
                        logger.info(f"Average transfer rate: {buffer_manager.get_average_transfer_rate()/1024/1024:.2f}MB/s")
                else:
                    transfer.send_message(sock, "Failed to receive file")
                    
            elif command.startswith("GET_FILE"):
                # Send file using adaptive or standard method based on configuration
                _, file_name = command.split(":", 1)
                use_adaptive = DEBUG_CONFIG.get("use_adaptive_transfer", True)
                logger.info(f"Client requested file: {file_name} (using {'adaptive' if use_adaptive else 'standard'} buffering)")
                
                # Create a demo file if it doesn't exist
                if not os.path.exists(file_name):
                    with open(file_name, 'w') as f:
                        f.write(f"This is a test file '{file_name}' from the local machine (reverse tunnel).\n" * 10)
                
                if use_adaptive:
                    # Try adaptive send first, fallback to standard method
                    success = transfer.send_file_adaptive(sock, file_name, buffer_manager, latency)
                    if not success:
                        logger.warning("Adaptive send failed, falling back to standard method")
                        success = transfer.send_file(sock, file_name)
                else:
                    # Use standard method directly
                    success = transfer.send_file(sock, file_name)
                    
                if success:
                    logger.info(f"File {file_name} sent successfully")
                    if use_adaptive:
                        logger.info(f"Final buffer size used: {buffer_manager.get_buffer_size()/1024:.2f}KB")
                        logger.info(f"Average transfer rate: {buffer_manager.get_average_transfer_rate()/1024/1024:.2f}MB/s")
                else:
                    logger.error(f"Failed to send file {file_name}")
                    
            else:
                transfer.send_message(sock, f"Unknown command: {command}")
                
        except Exception as e:
            logger.error(f"Error in file server handler: {e}")

def run_server(port: int, mode: str = "message"):
    """
    Run a server on the local machine that listens for connections coming through the reverse tunnel
    
    Args:
        port: Port to listen on
        mode: Server mode, either "message" or "file"
    """
    transfer = SocketTransferSubject()
    
    if mode == "file":
        handler = file_server_handler
        logger.info(f"Starting file server on local port {port}")
    else:
        handler = message_server_handler
        logger.info(f"Starting message server on local port {port}")
    
    transfer.run_server(port, handler)

def simulate_client_message_exchange(jump_server: str, remote_port: int) -> bool:
    """
    Simulate a client connecting to the jump server's remote port for message exchange
    
    Args:
        jump_server: Jump server hostname or IP
        remote_port: Remote port on jump server
    """
    transfer = SocketTransferSubject()
    logger.info(f"Simulating message client connecting to {jump_server}:{remote_port}")
    
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
            test_message = f"Test message {i+1} from simulated client"
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
        logger.error(f"Error in message exchange simulation: {e}")
        return False

def simulate_client_file_exchange(
    jump_server: str, 
    remote_port: int, 
    file_to_send: str = "", 
    file_to_get: str = ""
) -> bool:
    """
    Simulate a client connecting to the jump server's remote port for file exchange
    
    Args:
        jump_server: Jump server hostname or IP
        remote_port: Remote port on jump server
        file_to_send: Path to a file to send to the server (optional)
        file_to_get: Name of a file to get from the server (optional)
    """
    transfer = SocketTransferSubject()
    buffer_manager = BufferManager()
    observer = create_observer_if_enabled()
    
    logger.info(f"Simulating file client connecting to {jump_server}:{remote_port}")
    
    sock = transfer.connect_to_server(jump_server, remote_port)
    if not sock:
        return False

    # add observer to the transfer subject and start the observer if available
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
            
            # Send a file if requested
            file_sent_path = Path(file_to_send).expanduser()
            file_received_name = Path(file_to_get).expanduser()
            use_adaptive = DEBUG_CONFIG.get("use_adaptive_transfer", True)
            
            if file_to_send:
                if not file_sent_path.exists():
                    # Create a test file
                    with open(file_sent_path, 'w') as f:
                        f.write(f"This is a test file from the simulated client for reverse tunnel testing.\n" * 100)
                    logger.info(f"Created test file: {file_sent_path}")
                
                # Tell server we want to send a file
                transfer.send_message(sock, "SEND_FILE")
                
                # Send the file using adaptive or standard method
                logger.info(f"Sending file {file_sent_path} using {'adaptive' if use_adaptive else 'standard'} buffering")
                if use_adaptive:
                    success = transfer.send_file_adaptive(sock, file_sent_path, buffer_manager, latency)
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
                    return False
            
            # Get a file if requested
            elif file_received_name:
                # Tell server we want to get a file
                transfer.send_message(sock, f"GET_FILE:{file_received_name}")
                
                # Receive the file using adaptive or standard method
                output_dir = DEBUG_CONFIG.get("received_files_dir", "received_files")
                output_path = Path(output_dir).expanduser()
                output_path.mkdir(parents=True, exist_ok=True)
                
                logger.info(f"Receiving file {file_received_name} using {'adaptive' if use_adaptive else 'standard'} buffering")
                if use_adaptive:
                    file_path = transfer.receive_file_adaptive(sock, output_path, buffer_manager, latency)
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
                    return False
            
            sock.close()
            return True
        except Exception as e:
            logger.error(f"Error in file exchange simulation: {e}")
            return False

# 这个全局变量包含脚本的调试配置
# 直接修改这些值来调试脚本，无需使用命令行参数
# 注意：可通过 use_adaptive_transfer 选项控制是否使用自适应缓冲区大小优化文件传输性能
# ===== DEBUG CONFIGURATION =====
DEBUG_CONFIG = {
    # 服务器配置
    # "jump_server": "20.30.80.249",      # 跳转服务器的域名或 IP
    # "jump_user": "zfwj",     # 跳转服务器的用户名
    # "jump_port": 22,                  # 跳转服务器的 SSH 端口
    
    "jump_server": "192.168.31.123",      # 跳转服务器的域名或 IP
    "jump_user": "root",     # 跳转服务器的用户名
    "jump_port": 22,                  # 跳转服务器的 SSH 端口
    
    # 认证方式
    "use_password": True,            # 设置为 True 表示使用密码认证
    "identity_file": None,            # SSH 私钥路径 (例如 "~/.ssh/id_rsa")
    "password": None,                 # 密码 (如果 use_password=True)
    
    # 端口配置
    "remote_port": 8022,             # 跳转服务器上暴露的端口
    "local_port": 9022,              # 要转发到的本地端口
    
    # 运行模式选项
    "mode": "file",                  # 可选值: "message" 或 "file"
    "start_server": True,            # 设置为 True 表示在本地端口上运行服务器
    "simulate_client": True,         # 设置为 True 表示模拟客户端连接到远程端口
    
    # 文件传输选项 (当 mode="file" 时)
    # "send_file": "~/Anaconda3-2023.03-Linux-x86_64.sh",    # 模拟客户端要发送的文件路径
    "send_file": "~/Anaconda3-2024.10-1-Linux-x86_64.sh",    # 模拟客户端要发送的文件路径
    "get_file": "",                 # 模拟客户端要获取的文件名，空字符串表示不获取
    "received_files_dir": "~/received_files",
    "use_adaptive_transfer": True,  # 设置为 True 使用自适应传输，False 使用标准传输
    
    # 进度观察者选项
    "use_progress_observer": True,  # 设置为 True 启用进度观察者
    "use_rich_progress": True       # 设置为 True 使用 Rich 进度条（如果可用），False 使用简单进度输出
}
# =============================

def main():
    # 从全局调试配置中提取参数
    jump_server = DEBUG_CONFIG["jump_server"]
    jump_user = DEBUG_CONFIG["jump_user"]
    jump_port = DEBUG_CONFIG["jump_port"]
    use_password = DEBUG_CONFIG["use_password"]
    identity_file = DEBUG_CONFIG["identity_file"]
    password = DEBUG_CONFIG["password"]
    remote_port = DEBUG_CONFIG["remote_port"]
    local_port = DEBUG_CONFIG["local_port"]
    mode = DEBUG_CONFIG["mode"]
    start_server = DEBUG_CONFIG["start_server"]
    simulate_client = DEBUG_CONFIG["simulate_client"]
    send_file = DEBUG_CONFIG["send_file"]
    get_file = DEBUG_CONFIG["get_file"]
    
    # 创建 SSH 配置
    ssh_config = SSHConfig(
        jump_server=jump_server,
        jump_user=jump_user,
        jump_port=jump_port,
        identity_file=identity_file,
        use_password=use_password
    )
    
    # 如果选择了密码认证并且没有提供密码
    if use_password and not ssh_config.password:
        if password:
            ssh_config.password = password
        else:
            import getpass
            # prompt for password
            ssh_config.password = getpass.getpass(f"Password for {jump_user}@{jump_server}: ")

    # 如果请求运行服务器，在单独的线程中启动它
    server_thread = None
    if start_server:
        logger.info(f"Starting {mode} server on local port {local_port}")
        server_thread = threading.Thread(
            target=run_server,
            args=(local_port, mode),
            daemon=True
        )
        server_thread.start()
        time.sleep(1)  # 给服务器一些启动时间
    
    # 创建并建立反向 SSH 隧道
    logger.info(f"Establishing reverse tunnel between 2 devices:")
    logger.info(f"Device 1 (Local Machine): localhost:{local_port}")
    logger.info(f"Device 2 (Jump Server): {jump_server}:{remote_port}")
    
    reverse_tunnel = SSHTunnelReverse(
        ssh_config=ssh_config,
        remote_port=remote_port,
        local_host="localhost",
        local_port=local_port
    )
    
    if reverse_tunnel.establish_tunnel():
        logger.info("Reverse tunnel established successfully")
        
        # 如果请求，模拟客户端连接到隧道
        if simulate_client:
            time.sleep(2)  # 给隧道一些稳定的时间
            logger.info("Simulating client connection through the tunnel...")
            
            if mode == "file":
                if simulate_client_file_exchange(jump_server, remote_port, 
                                               send_file, get_file):
                    logger.info("File exchange simulation successful")
                else:
                    logger.error("File exchange simulation failed")
            else:
                if simulate_client_message_exchange(jump_server, remote_port):
                    logger.info("Message exchange simulation successful")
                else:
                    logger.error("Message exchange simulation failed")
        
        # Keep the tunnel open
        try:
            logger.info("Tunnel is active. Press Ctrl+C to stop the tunnel")
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Stopping tunnel...")
        finally:
            # 清理共享的 Rich Progress Observer
            if (RICH_AVAILABLE and 
                DEBUG_CONFIG.get("use_progress_observer", False) and 
                DEBUG_CONFIG.get("use_rich_progress", True)):
                try:
                    from core.rich_progress_observer import shutdown_shared_rich_observer
                    shutdown_shared_rich_observer()
                except Exception as e:
                    logger.error(f"Error shutting down shared observer: {e}")
    else:
        logger.error("Failed to establish tunnel")
        # 即使隧道建立失败也要清理
        if (RICH_AVAILABLE and 
            DEBUG_CONFIG.get("use_progress_observer", False) and 
            DEBUG_CONFIG.get("use_rich_progress", True)):
            try:
                from core.rich_progress_observer import shutdown_shared_rich_observer
                shutdown_shared_rich_observer()
            except Exception as e:
                logger.error(f"Error shutting down shared observer: {e}")
    
    # Clean up
    if server_thread and server_thread.is_alive():
        logger.info("Stopping server...")

if __name__ == "__main__":
    main()
