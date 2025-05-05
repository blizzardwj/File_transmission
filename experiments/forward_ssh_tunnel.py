#!/usr/bin/env python3
"""
Forward SSH Tunnel Experiment

This script demonstrates establishing a forward SSH tunnel through a jump server.
In a forward tunnel, local ports are forwarded to remote destinations through the jump server.

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

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('forward_tunnel_experiment')

def run_simple_server(port):
    """
    Run a simple TCP server that sends a welcome message to any client that connects
    """
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    
    try:
        server_socket.bind(('0.0.0.0', port))
        server_socket.listen(5)
        logger.info(f"Simple server is running on port {port}")
        
        while True:
            client_socket, address = server_socket.accept()
            logger.info(f"Client connected from {address}")
            
            # Send welcome message to client
            welcome_message = f"Hello from the simple server! Connected to port {port}\n"
            client_socket.send(welcome_message.encode())
            
            # Receive any data from client
            data = client_socket.recv(1024)
            if data:
                logger.info(f"Received data: {data.decode().strip()}")
                # Echo back the data
                client_socket.send(f"You said: {data.decode()}".encode())
            
            client_socket.close()
            
    except KeyboardInterrupt:
        logger.info("Server stopped by user")
    except Exception as e:
        logger.error(f"Server error: {e}")
    finally:
        server_socket.close()

def test_connection(host, port):
    """
    Test connection to the server by sending a message and receiving a response
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect((host, port))
        
        # Receive welcome message
        data = sock.recv(1024)
        logger.info(f"Received: {data.decode().strip()}")
        
        # Send test message
        test_message = "This is a test message from the client"
        sock.send(test_message.encode())
        
        # Receive echo
        data = sock.recv(1024)
        logger.info(f"Received: {data.decode().strip()}")
        
        sock.close()
        return True
    except Exception as e:
        logger.error(f"Connection test failed: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description="Forward SSH Tunnel Experiment")
    parser.add_argument("--jump-server", required=True, help="Jump server hostname or IP")
    parser.add_argument("--jump-user", required=True, help="Username for the jump server")
    parser.add_argument("--jump-port", type=int, default=22, help="SSH port on jump server (default: 22)")
    parser.add_argument("--identity-file", help="SSH identity file (private key)")
    parser.add_argument("--use-password", action="store_true", help="Use password auth instead of key")
    parser.add_argument("--local-port", type=int, default=8080, help="Local port to forward from (default: 8080)")
    parser.add_argument("--remote-host", default="localhost", help="Remote host to forward to (default: localhost)")
    parser.add_argument("--remote-port", type=int, default=80, help="Remote port to forward to (default: 80)")
    parser.add_argument("--run-server", action="store_true", help="Run a simple server on remote port (for testing)")
    args = parser.parse_args()
    
    # Create SSH config
    ssh_config = SSHConfig(
        jump_server=args.jump_server,
        jump_user=args.jump_user,
        jump_port=args.jump_port,
        identity_file=args.identity_file,
        use_password=args.use_password
    )
    
    # If password auth is selected, prompt for password
    if args.use_password and not ssh_config.password:
        import getpass
        ssh_config.password = getpass.getpass(f"Password for {args.jump_user}@{args.jump_server}: ")
    
    # If running the server is requested, start it in a separate thread
    server_thread = None
    if args.run_server:
        logger.info(f"Starting simple server on port {args.remote_port}")
        server_thread = threading.Thread(
            target=run_simple_server,
            args=(args.remote_port,),
            daemon=True
        )
        server_thread.start()
        time.sleep(1)  # Give the server time to start
    
    # Create and establish SSH tunnel
    tunnel = SSHTunnelForward(
        ssh_config=ssh_config,
        local_port=args.local_port,
        remote_host=args.remote_host,
        remote_port=args.remote_port
    )
    
    logger.info(f"Establishing forward tunnel: localhost:{args.local_port} -> {args.remote_host}:{args.remote_port}")
    if tunnel.establish_tunnel():
        logger.info("Tunnel established successfully")
        
        # Test the connection through the tunnel
        logger.info(f"Testing connection to localhost:{args.local_port}...")
        if test_connection("localhost", args.local_port):
            logger.info("Connection test successful")
        
        # Keep the tunnel open
        try:
            logger.info("Press Ctrl+C to stop the tunnel")
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
