#!/usr/bin/env python3
"""
Forward SSH Tunnel Experiment

This script demonstrates establishing a forward SSH tunnel between two devices:
1. Local Machine: Where this script runs
    Acts as a client that connects to services on jump server through tunnel.

2. Jump Server: Remote SSH server that runs target services.
    Should have services running on the specified remote port.

**BEFORE RUNNING THIS SCRIPT:**
1. Start a service on the jump server: 
   - For text/data testing: `nc -l -p 8080` 
   - For HTTP testing: `python3 -m http.server 8080`
   - For file transfer testing: `nc -l -p 8080 > received_file.dat`
2. Ensure the service is listening on the port you specify as --remote-port

CAUTION:
- Check nc version. Some versions need `-l -p` for listening, others just `-l`.

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
    
    # Interactive test (includes file transfer option)
    python forward_ssh_tunnel.py --jump-server jumphost.example.com --jump-user admin --local-port 8080 --remote-port 8080 --test interactive
    
    # Direct file transfer test
    python forward_ssh_tunnel.py --jump-server jumphost.example.com --jump-user admin --local-port 8080 --remote-port 8080 --test file_transfer
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

# readline will be imported conditionally when needed for interactive sessions
# to avoid conflicts with getpass.getpass()

#oAdd parent directory to path to import ssh_utils
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.utils import build_logger
from core.ssh_utils import SSHConfig, SSHTunnelForward

logger = build_logger(__name__)

# Global flag to track if readline has been initialized
_readline_initialized = False

def init_readline_if_tty():
    """
    Initialize readline support if running in a TTY and not already initialized.
    This helps avoid conflicts with getpass.getpass() by importing readline
    only when needed for interactive input sessions.
    """
    global _readline_initialized
    if not _readline_initialized and sys.stdin.isatty():
        try:
            import readline
            _readline_initialized = True
            logger.debug("Readline support enabled for interactive input.")
        except ImportError:
            # readline might not be available on all systems (e.g., some Windows setups)
            logger.debug("Warning: readline module not available. Advanced line editing features may be limited.")
            pass

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

def test_file_transfer_service(local_port: int, file_to_send_path: str) -> bool:
    """
    Test file transfer service running on jump server through the tunnel
    
    Args:
        local_port: Local port that forwards to jump server
        file_to_send_path: Path to the local file to send
        
    Returns:
        bool: True if transfer successful, False otherwise
    """
    try:
        file_path = Path(file_to_send_path)
        if not file_path.exists():
            logger.error(f"File not found: {file_to_send_path}")
            return False
        
        if not file_path.is_file():
            logger.error(f"Path is not a file: {file_to_send_path}")
            return False
        
        file_size = file_path.stat().st_size
        logger.info(f"Starting file transfer: {file_path.name} ({file_size:,} bytes)")
        logger.info(f"Connecting to tunnel at localhost:{local_port}")
        
        # Connect to the tunnel port
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(30)  # 30 second timeout for large files
        sock.connect(('localhost', local_port))
        
        # Send the file in chunks
        bytes_sent = 0
        chunk_size = 64 * 1024  # 64KB chunks
        
        with open(file_path, 'rb') as file:
            while True:
                chunk = file.read(chunk_size)
                if not chunk:
                    break
                
                sock.sendall(chunk)
                bytes_sent += len(chunk)
                
                # Log progress for larger files
                if file_size > 0:
                    progress = (bytes_sent / file_size) * 100
                    if bytes_sent % (chunk_size * 16) == 0 or bytes_sent == file_size:  # Log every ~1024KB or at end
                        logger.info(f"Progress: {bytes_sent:,}/{file_size:,} bytes ({progress:.1f}%)")
        
        sock.close()
        
        logger.info(f"File transfer completed successfully: {bytes_sent:,} bytes sent")
        return True
        
    except Exception as e:
        logger.error(f"File transfer failed: {e}")
        return False

def run_interactive_test(local_port: int, test_type: str):
    """
    Run interactive test session with the jump server service
    
    Args:
        local_port: Local port that forwards to jump server
        test_type: Type of test ('netcat', 'http', or 'file_transfer')
    """
    init_readline_if_tty()  # Enable readline for interactive input sessions
    
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
        
    elif test_type == 'file_transfer':
        logger.info("Starting interactive file transfer test session")
        logger.info("Enter file paths to transfer. Type 'quit' to exit.")
        
        while True:
            file_path = input("Enter file path to send (or 'quit'): ").strip()
            if file_path.lower() in ['quit', 'exit']:
                break
                
            if not file_path:
                # Use default file from DEBUG_CONFIG if no path entered
                file_path = DEBUG_CONFIG.get("file_transfer_source_path", "experiments/test_data/test_file2.dat")
                logger.info(f"Using default file: {file_path}")
            
            # Check if file exists, if not try to create a test file
            if not Path(file_path).exists():
                logger.warning(f"File not found: {file_path}")
                create_test = input("Create a test file? (y/n): ").strip().lower()
                if create_test in ['y', 'yes']:
                    try:
                        Path(file_path).parent.mkdir(parents=True, exist_ok=True)
                        with open(file_path, 'wb') as f:
                            test_data = b"Test file content for SSH tunnel file transfer\n" * 100  # Create ~4KB test file
                            f.write(test_data)
                        logger.info(f"Test file created: {file_path}")
                    except Exception as e:
                        logger.error(f"Failed to create test file: {e}")
                        continue
                else:
                    continue
            
            # Perform the file transfer
            success = test_file_transfer_service(local_port, file_path)
            if success:
                logger.info("File transfer completed successfully!")
            else:
                logger.error("File transfer failed!")
                
        logger.info("Interactive file transfer session ended")

# 这个全局变量包含脚本的调试配置
# 直接修改这些值来调试脚本，无需使用命令行参数
# ===== DEBUG CONFIGURATION =====
DEBUG_CONFIG = {
    # 服务器配置
    # "jump_server": "20.30.80.249",      # 跳转服务器的域名或 IP
    # "jump_user": "zfwj",                 # 跳转服务器的用户名
    # "jump_port": 22,                     # 跳转服务器的 SSH 端口
    
    # "jump_server": "192.168.31.123",   # 跳转服务器的域名或 IP (备用)
    # "jump_user": "root",               # 跳转服务器的用户名 (备用)
    # "jump_port": 22,                   # 跳转服务器的 SSH 端口 (备用)
    
    "jump_server": "45.145.74.109",   # 跳转服务器的域名或 IP (备用)
    "jump_user": "root",               # 跳转服务器的用户名 (备用)
    "jump_port": 5222,                   # 跳转服务器的 SSH 端口 (备用)

    # 认证方式
    "use_password": True,                # 设置为 True 表示使用密码认证
    "identity_file": None,               # SSH 私钥路径 (例如 "~/.ssh/id_rsa")
    "password": None,                    # 密码 (如果 use_password=True)
    
    # 端口配置
    "local_port": 9080,                  # 本地转发端口
    "remote_port": 8080,                 # 跳转服务器上目标服务的端口
    
    # 测试选项
    "test": "interactive",               # 可选值: "netcat", "http", "interactive", "file_transfer", None
    "file_transfer_source_path": "experiments/test_data/test_file_2.dat",  # 要发送的本地文件路径
    "wait_time": 5,                      # 建立隧道后等待时间
}
# =============================

def main():
    # 使用调试配置
    logger.info("Using DEBUG_CONFIG for forward SSH tunnel.")
    
    # 从调试配置提取参数
    jump_server = DEBUG_CONFIG["jump_server"]
    jump_user = DEBUG_CONFIG["jump_user"]
    jump_port = DEBUG_CONFIG["jump_port"]
    use_password = DEBUG_CONFIG["use_password"]
    identity_file = DEBUG_CONFIG["identity_file"]
    password = DEBUG_CONFIG["password"]
    local_port = DEBUG_CONFIG["local_port"]
    remote_port = DEBUG_CONFIG["remote_port"]
    test_type = DEBUG_CONFIG["test"]
    wait_time = DEBUG_CONFIG["wait_time"]
        
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
            # Conditional import of getpass
            import getpass
            ssh_config.password = getpass.getpass(f"Password for {jump_user}@{jump_server}: ")

    # Prepare forward SSH tunnel object
    forward_tunnel = SSHTunnelForward(
        ssh_config=ssh_config,
        local_port=local_port,  # Local port to listen on (from DEBUG_CONFIG)
        remote_host="localhost", # Target service is on the jump server itself (localhost from its perspective)
        remote_port=remote_port  # Target service port on the jump server (from DEBUG_CONFIG)
    )
    
    try:
        logger.info("Attempting to establish forward SSH tunnel...")
        logger.info(f"  Local endpoint for tunnel: localhost:{forward_tunnel.local_port}")
        logger.info(f"  Remote service (via {ssh_config.jump_server} ({ssh_config.jump_user})): {forward_tunnel.remote_host}:{forward_tunnel.remote_port}")
        
        if forward_tunnel.establish_tunnel():
            logger.info("Forward SSH tunnel established successfully.")
            actual_local_port = forward_tunnel.local_port # The local port the tunnel is listening on
            logger.info(f"  Tunnel active: localhost:{actual_local_port} <-> {ssh_config.jump_server} ({forward_tunnel.remote_host}:{forward_tunnel.remote_port})")

            if wait_time > 0:
                logger.info(f"Waiting {wait_time} seconds for tunnel to stabilize...")
                time.sleep(wait_time)
            
            # Run tests if specified
            if test_type:
                if test_type == 'netcat':
                    test_netcat_service(actual_local_port)
                elif test_type == 'http':
                    test_http_service(actual_local_port)
                elif test_type == 'file_transfer':
                    file_path = DEBUG_CONFIG.get("file_transfer_source_path", "experiments/test_data/test_file_2.dat")
                    test_file_transfer_service(actual_local_port, file_path)
                elif test_type == 'interactive':
                    init_readline_if_tty()  # Enable readline for interactive input
                    logger.info("Interactive mode selected. Choose test type for the session:")
                    logger.info("  1. Netcat")
                    logger.info("  2. HTTP")
                    logger.info("  3. File Transfer")
                    choice = input("Enter choice (1, 2, or 3): ")
                    
                    effective_test_type = None
                    if choice == '1':
                        effective_test_type = 'netcat'
                    elif choice == '2':
                        effective_test_type = 'http'
                    elif choice == '3':
                        effective_test_type = 'file_transfer'
                    else:
                        logger.warning("Invalid choice. Defaulting to netcat for interactive session.")
                        effective_test_type = 'netcat'
                    
                    run_interactive_test(actual_local_port, effective_test_type)
            
            logger.info("Tunnel is active. Press Ctrl+C to stop.")
            logger.info(f"You can manually test by connecting to localhost:{actual_local_port}")
            while True:
                if not forward_tunnel.is_active:
                    logger.warning("Tunnel appears to have dropped. Exiting keep-alive loop.")
                    break
                time.sleep(1) # Keep main thread alive
        else:
            logger.error(f"Failed to establish forward tunnel. Check SSH settings, server availability, and network.")

    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received. Shutting down...")
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}", exc_info=True) # Log traceback for debugging
    finally:
        # Ensure tunnel is stopped if it was started
        if forward_tunnel and forward_tunnel.is_active:
            logger.info("Stopping forward SSH tunnel...")
            if hasattr(forward_tunnel, 'close_tunnel'):
                forward_tunnel.close_tunnel()
                logger.info("Forward SSH tunnel stopped.")
            else:
                # This case should ideally not be reached if SSHTunnelForward inherits from SSHTunnelBase correctly
                logger.warning("Tunnel object does not have a close_tunnel() method. Manual cleanup might be needed.")
        else:
            logger.info("No active tunnel to stop, or tunnel was not established.")

if __name__ == "__main__":
    main()
