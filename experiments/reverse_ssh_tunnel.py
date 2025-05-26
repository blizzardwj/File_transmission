#!/usr/bin/env python3
"""
Reverse SSH Tunnel Experiment

This script demonstrates establishing a reverse SSH tunnel between two devices:
1. Local Machine: Where this script runs
    Act as a real socket server (Server).
    And simulate a file sender (Sender).
    Actually, there are two objects on the local machine.
    
2. Jump Server: Remote SSH server that accepts the reverse tunnel
    Act as a proxy server (Proxy) that forwards the traffic to the real socket server.
    And receive the file from the sender.

In a reverse tunnel, a port on the remote jump server is forwarded to a local port on your machine.
This allows external clients to connect to the jump server's port and have their traffic forwarded to 
your local machine, which is useful when your machine is behind a firewall or NAT.

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
import socket
import logging
import argparse
import threading

# Add parent directory to path to import ssh_utils
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.ssh_utils import SSHConfig, SSHTunnelReverse
from core.socket_data_transfer import SocketDataTransfer

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('reverse_tunnel_experiment')

def message_server_handler(sock: socket.socket) -> None:
    """
    Handle a client connection for message exchange server
    This runs on the local machine and receives connections via the jump server
    """
    transfer = SocketDataTransfer()
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
    transfer = SocketDataTransfer()
    try:
        # Send welcome message, give the name or ip of localhost
        localhost = socket.gethostname()
        sock_name = sock.getsockname()
        transfer.send_message(sock, f"Hello from the {localhost}! This is a file exchange service accessed via reverse tunnel. I am listening on port {sock_name[1]}")
        
        # Wait for command
        command = transfer.receive_message(sock)
        if not command:
            logger.info("Client disconnected")
            return
            
        if command.startswith("SEND_FILE"):
            # Receive file
            logger.info("Client wants to send a file via reverse tunnel")
            output_dir = "received_files"
            os.makedirs(output_dir, exist_ok=True)
            
            file_path = transfer.receive_file(sock, output_dir)
            if file_path:
                transfer.send_message(sock, f"File received and saved as {file_path}")
            else:
                transfer.send_message(sock, "Failed to receive file")
                
        elif command.startswith("GET_FILE"):
            # Send file
            _, file_name = command.split(":", 1)
            logger.info(f"Client requested file: {file_name}")
            
            # Create a demo file if it doesn't exist
            if not os.path.exists(file_name):
                with open(file_name, 'w') as f:
                    f.write(f"This is a test file '{file_name}' from the local machine (reverse tunnel).\n" * 10)
                    
            if transfer.send_file(sock, file_name):
                logger.info(f"File {file_name} sent successfully")
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
    transfer = SocketDataTransfer()
    
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
    transfer = SocketDataTransfer()
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
    transfer = SocketDataTransfer()
    logger.info(f"Simulating file client connecting to {jump_server}:{remote_port}")
    
    sock = transfer.connect_to_server(jump_server, remote_port)
    if not sock:
        return False
    
    try:
        # Receive welcome message
        welcome = transfer.receive_message(sock)
        if welcome:
            logger.info(f"Server: {welcome}")
        
        # Send a file if requested
        if file_to_send:
            if not os.path.exists(file_to_send):
                # Create a test file
                with open(file_to_send, 'w') as f:
                    f.write(f"This is a test file from the simulated client for reverse tunnel testing.\n" * 100)
                logger.info(f"Created test file: {file_to_send}")
            
            # Tell server we want to send a file
            transfer.send_message(sock, "SEND_FILE")
            
            # Send the file
            if transfer.send_file(sock, file_to_send):
                logger.info(f"File {file_to_send} sent successfully")
                
                # Get server response
                response = transfer.receive_message(sock)
                if response:
                    logger.info(f"Server: {response}")
            else:
                logger.error(f"Failed to send file {file_to_send}")
                return False
        
        # Get a file if requested
        elif file_to_get:
            # Tell server we want to get a file
            transfer.send_message(sock, f"GET_FILE:{file_to_get}")
            
            # Receive the file
            output_dir = "downloaded_files"
            os.makedirs(output_dir, exist_ok=True)
            
            file_path = transfer.receive_file(sock, output_dir)
            if file_path:
                logger.info(f"File received and saved as {file_path}")
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
# ===== DEBUG CONFIGURATION =====
DEBUG_CONFIG = {
    # 服务器配置
    "jump_server": "20.30.80.249",      # 跳转服务器的域名或 IP
    "jump_user": "zfwj",     # 跳转服务器的用户名
    "jump_port": 22,                  # 跳转服务器的 SSH 端口
    
    # "jump_server": "192.168.31.123",      # 跳转服务器的域名或 IP
    # "jump_user": "root",     # 跳转服务器的用户名
    # "jump_port": 22,                  # 跳转服务器的 SSH 端口
    
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
    "send_file": "/home/adminwj/Anaconda3-2023.03-Linux-x86_64.sh",    # 模拟客户端要发送的文件路径
    # "send_file": "/home/jytong/Anaconda3-2024.10-1-Linux-x86_64.sh",    # 模拟客户端要发送的文件路径
    "get_file": "",                 # 模拟客户端要获取的文件名，空字符串表示不获取
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
    else:
        logger.error("Failed to establish tunnel")
    
    # Clean up
    if server_thread and server_thread.is_alive():
        logger.info("Stopping server...")

if __name__ == "__main__":
    main()
