#!/usr/bin/env python3
"""
Forward SSH Tunnel Experiment

This script demonstrates establishing a forward SSH tunnel between two devices:
1. Local Machine: Where this script runs
    Acts as a client that connects to services on jump server through tunnel.

2. Jump Server: Remote SSH server that runs target services.
    Should have services running on the specified remote port.

BEFORE RUNNING THIS SCRIPT:
1. Start a service on the jump server: `nc -l 8080` or `python -m http.server 8080`
2. Ensure the service is listening on the port you specify as --remote-port

FORWARD TUNNEL MECHANISM:
In a forward tunnel, a local port on your machine is forwarded to a port on the jump server.
This allows you to access services running on the jump server through your local port.

Data Flow:
Local Machine:local_port -> Forward SSH Tunnel -> Jump Server:remote_port -> Target Service

CONFIGURATION REQUIREMENTS:
1. Ensure the jump server allows SSH connections and port forwarding.
2. Start the target service on the jump server before running this script.

Usage:
    python forward_ssh_tunnel.py --jump-server <server> --jump-user <user> --local-port <port> --remote-port <port> [--test <type>]

Examples:
    # Test netcat service
    python forward_ssh_tunnel.py --jump-server jumphost.example.com --jump-user admin --local-port 8080 --remote-port 8080 --test netcat
    
    # Test HTTP service
    python forward_ssh_tunnel.py --jump-server jumphost.example.com --jump-user admin --local-port 8080 --remote-port 8000 --test http
    
    # Interactive test
    python forward_ssh_tunnel.py --jump-server jumphost.example.com --jump-user admin --local-port 8080 --remote-port 8080 --test interactive
"""

import os
import sys
import time
import socket
import logging
import argparse
import threading
import urllib.request
import urllib.error
from pathlib import Path

# Add parent directory to path to import ssh_utils
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.ssh_utils import SSHConfig, SSHTunnelForward

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('forward_tunnel_experiment')

def test_netcat_service(local_port: int) -> bool:
    """
    Test netcat service running on jump server through the tunnel
    
    Args:
        local_port: Local port that forwards to jump server
        
    Returns:
        bool: True if test successful, False otherwise
    """
    try:
        logger.info(f"Testing netcat service on localhost:{local_port}")
        
        # Connect to the tunnel port
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(10)  # 10 second timeout
        sock.connect(('localhost', local_port))
        
        # Send test messages
        test_messages = [
            "Hello from local machine!",
            "Testing forward SSH tunnel",
            "Message 3: Tunnel working correctly"
        ]
        
        for msg in test_messages:
            logger.info(f"Sending: {msg}")
            sock.send((msg + '\n').encode('utf-8'))
            time.sleep(1)
        
        # Send final message and close
        sock.send("END\n".encode('utf-8'))
        sock.close()
        
        logger.info("Netcat test completed successfully")
        return True
        
    except Exception as e:
        logger.error(f"Netcat test failed: {e}")
        return False

def test_http_service(local_port: int) -> bool:
    """
    Test HTTP service running on jump server through the tunnel
    
    Args:
        local_port: Local port that forwards to jump server
        
    Returns:
        bool: True if test successful, False otherwise
    """
    try:
        url = f"http://localhost:{local_port}/"
        logger.info(f"Testing HTTP service at {url}")
        
        # Make HTTP request through the tunnel
        with urllib.request.urlopen(url, timeout=10) as response:
            content = response.read().decode('utf-8')
            status_code = response.getcode()
            
            logger.info(f"HTTP Response Status: {status_code}")
            logger.info(f"HTTP Response Content (first 200 chars): {content[:200]}...")
            
            if status_code == 200:
                logger.info("HTTP test completed successfully")
                return True
            else:
                logger.error(f"HTTP test failed with status code: {status_code}")
                return False
                
    except urllib.error.URLError as e:
        logger.error(f"HTTP test failed: {e}")
        return False
    except Exception as e:
        logger.error(f"HTTP test failed with unexpected error: {e}")
        return False

def run_interactive_test(local_port: int, test_type: str):
    """
    Run interactive test session with the jump server service
    
    Args:
        local_port: Local port that forwards to jump server
        test_type: Type of test ('netcat' or 'http')
    """
    if test_type == 'netcat':
        logger.info("Starting interactive netcat test session")
        logger.info("Type messages to send to jump server. Type 'quit' to exit.")
        
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect(('localhost', local_port))
            
            while True:
                message = input("Enter message: ").strip()
                if message.lower() in ['quit', 'exit']:
                    break
                    
                sock.send((message + '\n').encode('utf-8'))
                logger.info(f"Sent: {message}")
                
            sock.close()
            logger.info("Interactive netcat session ended")
            
        except Exception as e:
            logger.error(f"Interactive netcat test failed: {e}")
            
    elif test_type == 'http':
        logger.info("Starting interactive HTTP test session")
        logger.info("Available commands: '/' for root, '/path' for specific path, 'quit' to exit")
        
        while True:
            path = input("Enter HTTP path (or 'quit'): ").strip()
            if path.lower() in ['quit', 'exit']:
                break
                
            if not path.startswith('/'):
                path = '/' + path
                
            try:
                url = f"http://localhost:{local_port}{path}"
                logger.info(f"Requesting: {url}")
                
                with urllib.request.urlopen(url, timeout=10) as response:
                    content = response.read().decode('utf-8')
                    logger.info(f"Status: {response.getcode()}")
                    logger.info(f"Response: {content[:500]}...")
                    
            except Exception as e:
                logger.error(f"HTTP request failed: {e}")
                
        logger.info("Interactive HTTP session ended")

# 这个全局变量包含脚本的调试配置
# 直接修改这些值来调试脚本，无需使用命令行参数
# ===== DEBUG CONFIGURATION =====
DEBUG_CONFIG = {
    # 服务器配置
    "jump_server": "20.30.80.249",      # 跳转服务器的域名或 IP
    "jump_user": "zfwj",                 # 跳转服务器的用户名
    "jump_port": 22,                     # 跳转服务器的 SSH 端口
    
    # "jump_server": "192.168.31.123",   # 跳转服务器的域名或 IP (备用)
    # "jump_user": "root",               # 跳转服务器的用户名 (备用)
    # "jump_port": 22,                   # 跳转服务器的 SSH 端口 (备用)
    
    # 认证方式
    "use_password": True,                # 设置为 True 表示使用密码认证
    "identity_file": None,               # SSH 私钥路径 (例如 "~/.ssh/id_rsa")
    "password": None,                    # 密码 (如果 use_password=True)
    
    # 端口配置
    "local_port": 9080,                  # 本地转发端口
    "remote_port": 8080,                 # 跳转服务器上目标服务的端口
    
    # 测试选项
    "test": "interactive",               # 可选值: "netcat", "http", "interactive", None
    "wait_time": 5,                      # 建立隧道后等待时间
}
# =============================

def main():

    # 使用调试配置
    logger.info("Using DEBUG_CONFIG for quick debugging")
    
    # 从调试配置提取参数
    jump_server = DEBUG_CONFIG["jump_server"]
    jump_user = DEBUG_CONFIG["jump_user"]
    jump_port = DEBUG_CONFIG["jump_port"]
    local_port = DEBUG_CONFIG["local_port"]
    remote_port = DEBUG_CONFIG["remote_port"]
    test_type = DEBUG_CONFIG["test"]
    wait_time = DEBUG_CONFIG["wait_time"]
    use_password = DEBUG_CONFIG["use_password"]
    identity_file = DEBUG_CONFIG["identity_file"]
    password = DEBUG_CONFIG["password"]
        
    
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

    # Establish forward SSH tunnel
    forward_tunnel = SSHTunnelForward(
        ssh_config=ssh_config,
        local_port=local_port,
        remote_host="localhost",
        remote_port=remote_port
    )
    
    logger.info(f"Establishing forward tunnel: localhost:{local_port} -> {jump_server}:{remote_port}")
    if forward_tunnel.establish_tunnel():
        logger.info("Tunnel established successfully")
        
        # Wait for tunnel to stabilize
        logger.info(f"Waiting {wait_time} seconds for tunnel to stabilize...")
        time.sleep(wait_time)
        
        # Run tests if specified
        if test_type:
            if test_type == 'netcat':
                test_netcat_service(local_port)
            elif test_type == 'http':
                test_http_service(local_port)
            elif test_type == 'interactive':
                # Determine test type based on user's choice
                print("Choose test type:")
                print("1. Netcat")
                print("2. HTTP")
                choice = input("Enter choice (1 or 2): ")
                if choice == '1':
                    test_type = 'netcat'
                elif choice == '2':
                    test_type = 'http'
                else:
                    print("Invalid choice. Defaulting to netcat.")
                    test_type = 'netcat'
                
                if test_type == 'netcat':
                    run_interactive_test(local_port, 'netcat')
                elif test_type == 'http':
                    run_interactive_test(local_port, 'http')
        
        try:
            logger.info("Tunnel is active. Press Ctrl+C to stop the tunnel")
            logger.info(f"You can manually test the tunnel by connecting to localhost:{local_port}")
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Stopping tunnel...")
    else:
        logger.error("Failed to establish tunnel")

if __name__ == "__main__":
    main()
