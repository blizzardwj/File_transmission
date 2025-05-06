#!/usr/bin/env python3
"""
Reverse SSH Tunnel Experiment

This script demonstrates establishing a reverse SSH tunnel between two devices:
1. Local Machine: Where this script runs
2. Jump Server: Remote SSH server that accepts the reverse tunnel

In a reverse tunnel, a port on the remote jump server is forwarded to a local port on your machine.
This allows external clients to connect to the jump server's port and have their traffic forwarded to 
your local machine, which is useful when your machine is behind a firewall or NAT.

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
from core.tunnel_data_transfer import TunnelTransfer

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
    transfer = TunnelTransfer()
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
    transfer = TunnelTransfer()
    try:
        # Send welcome message
        transfer.send_message(sock, "Hello from the local machine! This is a file exchange service accessed via reverse tunnel.")
        
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
    transfer = TunnelTransfer()
    
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
    transfer = TunnelTransfer()
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

def simulate_client_file_exchange(jump_server: str, remote_port: int, file_to_send: str = None, file_to_get: str = None) -> bool:
    """
    Simulate a client connecting to the jump server's remote port for file exchange
    
    Args:
        jump_server: Jump server hostname or IP
        remote_port: Remote port on jump server
        file_to_send: Path to a file to send to the server (optional)
        file_to_get: Name of a file to get from the server (optional)
    """
    transfer = TunnelTransfer()
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

def main():
    parser = argparse.ArgumentParser(description="Reverse SSH Tunnel Experiment (2-device setup)")
    parser.add_argument("--jump-server", required=True, help="Jump server hostname or IP")
    parser.add_argument("--jump-user", required=True, help="Username for the jump server")
    parser.add_argument("--jump-port", type=int, default=22, help="SSH port on jump server (default: 22)")
    parser.add_argument("--identity-file", help="SSH identity file (private key)")
    parser.add_argument("--use-password", action="store_true", help="Use password auth instead of key")
    parser.add_argument("--remote-port", type=int, default=8022, help="Remote port on jump server to expose (default: 8022)")
    parser.add_argument("--local-port", type=int, default=9022, help="Local port to forward to (default: 9022)")
    parser.add_argument("--mode", choices=["message", "file"], default="message", 
                        help="Transfer mode: message or file (default: message)")
    parser.add_argument("--run-server", action="store_true", help="Run a server on local port")
    parser.add_argument("--simulate-client", action="store_true", help="Simulate a client connecting to the remote port")
    parser.add_argument("--send-file", help="Path to a file for simulated client to send (in file mode)")
    parser.add_argument("--get-file", help="Name of a file for simulated client to get (in file mode)")
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

    # Start local server if requested
    server_thread = None
    if args.run_server:
        logger.info(f"Starting {args.mode} server on local port {args.local_port}")
        server_thread = threading.Thread(
            target=run_server,
            args=(args.local_port, args.mode),
            daemon=True
        )
        server_thread.start()
        time.sleep(1)  # Give the server time to start
    
    # Create and establish reverse SSH tunnel
    logger.info(f"Establishing reverse tunnel between 2 devices:")
    logger.info(f"Device 1 (Local Machine): localhost:{args.local_port}")
    logger.info(f"Device 2 (Jump Server): {args.jump_server}:{args.remote_port}")
    
    tunnel = SSHTunnelReverse(
        ssh_config=ssh_config,
        remote_port=args.remote_port,
        local_host="localhost",
        local_port=args.local_port
    )
    
    if tunnel.establish_tunnel():
        logger.info("Reverse tunnel established successfully")
        
        # If requested, simulate a client connecting to the tunnel
        if args.simulate_client:
            time.sleep(2)  # Give tunnel time to stabilize
            logger.info("Simulating client connection through the tunnel...")
            
            if args.mode == "file":
                if simulate_client_file_exchange(args.jump_server, args.remote_port, 
                                               args.send_file, args.get_file):
                    logger.info("File exchange simulation successful")
                else:
                    logger.error("File exchange simulation failed")
            else:
                if simulate_client_message_exchange(args.jump_server, args.remote_port):
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
