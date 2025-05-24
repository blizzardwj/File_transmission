#!/usr/bin/env python3
"""
Forward SSH Tunnel Experiment

This script demonstrates establishing a forward SSH tunnel between two devices:
1. Local Machine: Where this script runs
2. Jump Server: Remote SSH server that will forward traffic

In a forward tunnel, a local port on your machine is forwarded to a port on the jump server.
This allows you to access services running on the jump server through your local port.

Usage:
    python forward_ssh_tunnel.py --jump-server <server> --jump-user <user> [options]

Example:
    python forward_ssh_tunnel.py --jump-server jumphost.example.com --jump-user admin --local-port 8080 --remote-port 80
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
from core.ssh_utils import SSHConfig, SSHTunnelForward
from core.socket_data_transfer import SocketDataTransfer

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('forward_tunnel_experiment')

def message_server_handler(sock: socket.socket) -> None:
    """
    Handle a client connection for message exchange server
    """
    transfer = SocketDataTransfer()
    try:
        # Send welcome message
        transfer.send_message(sock, "Hello from the jump server! This is a message exchange service.")
        
        # Receive and respond to messages
        while True:
            message = transfer.receive_message(sock)
            if not message:
                logger.info("Client disconnected")
                break
                
            logger.info(f"Received message: {message}")
            
            # Echo back the data with a prefix
            transfer.send_message(sock, f"You said: {message}")
            
            # Exit if requested
            if message.lower() in ["exit", "quit", "bye"]:
                break
                
    except Exception as e:
        logger.error(f"Error in message server handler: {e}")

def file_server_handler(sock: socket.socket) -> None:
    """
    Handle a client connection for file exchange server
    """
    transfer = SocketDataTransfer()
    try:
        # Send welcome message
        transfer.send_message(sock, "Hello from the jump server! This is a file exchange service.")
        
        # Wait for command
        command = transfer.receive_message(sock)
        if not command:
            logger.info("Client disconnected")
            return
            
        if command.startswith("SEND_FILE"):
            # Receive file
            logger.info("Client wants to send a file")
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
                    f.write(f"This is a test file '{file_name}' from the forward tunnel server.\n" * 10)
                    
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
    Run a server that listens for connections and handles them based on the specified mode
    
    Args:
        port: Port to listen on
        mode: Server mode, either "message" or "file"
    """
    transfer = SocketDataTransfer()
    
    if mode == "file":
        handler = file_server_handler
        logger.info(f"Starting file server on port {port}")
    else:
        handler = message_server_handler
        logger.info(f"Starting message server on port {port}")
    
    transfer.run_server(port, handler)

def test_message_exchange(host: str, port: int) -> bool:
    """
    Test message exchange through the tunnel
    """
    transfer = SocketDataTransfer()
    sock = transfer.connect_to_server(host, port)
    if not sock:
        return False
    
    try:
        # Receive welcome message
        welcome = transfer.receive_message(sock)
        if welcome:
            logger.info(f"Server: {welcome}")
        
        # Send test message
        test_message = "This is a test message from the local machine"
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
        logger.error(f"Error in message exchange test: {e}")
        return False

def test_file_exchange(host: str, port: int, file_to_send: str = None, file_to_get: str = None) -> bool:
    """
    Test file exchange through the tunnel
    
    Args:
        host: Server host
        port: Server port
        file_to_send: Path to a file to send to the server (optional)
        file_to_get: Name of a file to get from the server (optional)
    """
    transfer = SocketDataTransfer()
    sock = transfer.connect_to_server(host, port)
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
                    f.write(f"This is a test file from the local machine for forward tunnel testing.\n" * 100)
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
        logger.error(f"Error in file exchange test: {e}")
        return False

# 这个全局变量包含脚本的调试配置
# 直接修改这些变量来调试脚本，无需使用命令行参数
# ===== DEBUG CONFIGURATION =====
# 服务器配置
DEBUG_CONFIG = {
    # 服务器配置
    "jump_server": "example.com",      # 跳转服务器的域名或 IP
    "jump_user": "your_username",     # 跳转服务器的用户名
    "jump_port": 22,                  # 跳转服务器的 SSH 端口
    
    # 认证方式
    "use_password": False,            # 设置为 True 表示使用密码认证
    "identity_file": None,            # SSH 私钥路径 (例如 "~/.ssh/id_rsa")
    "password": None,                 # 密码 (如果 use_password=True)
    
    # 端口配置
    "local_port": 8080,              # 本地机器上的端口 (将被转发到远程端口)
    "remote_port": 80,               # 跳转服务器上的端口
    
    # 运行模式选项
    "mode": "file",                  # 可选值: "message" 或 "file"
    "start_server": True,            # 设置为 True 表示在跳转服务器的远程端口上运行服务器 (用于测试)
    
    # 文件传输选项 (当 mode="file" 时)
    "send_file": "test_file.txt",    # 要发送到服务器的文件路径
    "get_file": "",                 # 要从服务器获取的文件名，空字符串表示不获取
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
    local_port = DEBUG_CONFIG["local_port"]
    remote_port = DEBUG_CONFIG["remote_port"]
    mode = DEBUG_CONFIG["mode"]
    start_server = DEBUG_CONFIG["start_server"]  # 使用新的变量名
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
            ssh_config.password = getpass.getpass(f"Password for {jump_user}@{jump_server}: ")
    
    # 如果请求运行服务器，在单独的线程中启动它
    server_thread = None
    if start_server:  # 使用新的变量名
        logger.info(f"Starting {mode} server on port {remote_port}")
        # 创建一个函数引用作为目标
        server_thread = threading.Thread(
            target=run_server,
            args=(remote_port, mode),
            daemon=True
        )
        server_thread.start()
        time.sleep(1)  # 给服务器一些启动时间
    
    # 创建并建立 SSH 隧道
    # 在 2 设备的前向隧道中:
    # - local_port: 本地机器上的端口
    # - remote_host: 始终是 localhost (跳转服务器本身)
    # - remote_port: 跳转服务器上的端口
    forward_tunnel = SSHTunnelForward(
        ssh_config=ssh_config,
        local_port=local_port,
        remote_host="localhost",  # 始终目标是跳转服务器本身
        remote_port=remote_port
    )
    
    logger.info(f"Establishing forward tunnel between 2 devices:")
    logger.info(f"Device 1 (Local Machine): localhost:{local_port}")
    logger.info(f"Device 2 (Jump Server): {jump_server} -> localhost:{remote_port}")
    
    if forward_tunnel.establish_tunnel():
        logger.info("Tunnel established successfully")
        
        # 基于模式测试通过隧道的连接
        logger.info(f"Testing {mode} transfer through tunnel to localhost:{local_port}...")
        
        if mode == "file":
            # 现在get_file已经来自DEBUG_CONFIG，并且默认为空字符串
            if test_file_exchange("localhost", local_port, send_file, get_file):
                logger.info("File exchange test successful")
            else:
                logger.error("File exchange test failed")
        else:
            if test_message_exchange("localhost", local_port):
                logger.info("Message exchange test successful")
            else:
                logger.error("Message exchange test failed")
        
        # 保持隧道开放
        try:
            logger.info("Tunnel is active. Press Ctrl+C to stop the tunnel")
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Stopping tunnel...")
    else:
        logger.error("Failed to establish tunnel")
    
    # 清理
    if server_thread and server_thread.is_alive():
        logger.info("Stopping server...")

if __name__ == "__main__":
    main()
