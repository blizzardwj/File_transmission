#!/usr/bin/env python3
"""
Reverse SSH Tunnel Experiment

This script demonstrates establishing a reverse SSH tunnel through a jump server.
In a reverse tunnel, remote ports on the jump server are forwarded to local destinations.
This is useful when the target machine is behind a firewall or NAT.

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

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('reverse_tunnel_experiment')

def run_file_server(port):
    """
    Run a simple file server that listens for connections and can receive files
    """
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    
    try:
        server_socket.bind(('0.0.0.0', port))
        server_socket.listen(5)
        logger.info(f"File server is running on port {port}")
        
        while True:
            client_socket, address = server_socket.accept()
            logger.info(f"Client connected from {address}")
            
            # Send welcome message to client
            welcome_message = f"Connected to file server on port {port}. Send 'file:<filename>' to start transfer.\n"
            client_socket.send(welcome_message.encode())
            
            # Receive command
            data = client_socket.recv(1024).decode().strip()
            
            if data.startswith("file:"):
                filename = data[5:]
                logger.info(f"Preparing to receive file: {filename}")
                
                # Send acknowledgment
                client_socket.send(b"Ready to receive. Send file size followed by data.\n")
                
                # Receive file size
                size_data = client_socket.recv(1024).decode().strip()
                try:
                    file_size = int(size_data)
                    logger.info(f"File size: {file_size} bytes")
                    
                    # Send acknowledgment
                    client_socket.send(b"Size received. Start sending file.\n")
                    
                    # Receive file data
                    received = 0
                    with open(f"received_{filename}", 'wb') as f:
                        while received < file_size:
                            chunk = client_socket.recv(min(4096, file_size - received))
                            if not chunk:
                                break
                            f.write(chunk)
                            received += len(chunk)
                            # Print progress
                            percent = int(received * 100 / file_size)
                            print(f"\rReceiving: {percent}% ({received}/{file_size} bytes)", end='')
                    
                    print()  # New line after progress
                    
                    if received == file_size:
                        logger.info(f"File received successfully: received_{filename}")
                        client_socket.send(b"File received successfully.\n")
                    else:
                        logger.warning(f"Incomplete file received: {received}/{file_size} bytes")
                        client_socket.send(b"Warning: Incomplete file received.\n")
                        
                except ValueError:
                    logger.error("Invalid file size format")
                    client_socket.send(b"Error: Invalid file size format.\n")
            else:
                logger.info(f"Received command: {data}")
                client_socket.send(f"Unknown command: {data}\n".encode())
            
            client_socket.close()
            
    except KeyboardInterrupt:
        logger.info("Server stopped by user")
    except Exception as e:
        logger.error(f"Server error: {e}")
    finally:
        server_socket.close()

def send_file_via_tunnel(jump_server, remote_port, filename):
    """
    Send a file through the reverse tunnel by connecting to the jump server's remote port
    """
    if not os.path.exists(filename):
        logger.error(f"File not found: {filename}")
        return False
    
    file_size = os.path.getsize(filename)
    file_basename = os.path.basename(filename)
    
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect((jump_server, remote_port))
        
        # Receive welcome message
        data = sock.recv(1024)
        logger.info(f"Server: {data.decode().strip()}")
        
        # Send file command
        sock.send(f"file:{file_basename}".encode())
        
        # Wait for acknowledgment
        data = sock.recv(1024)
        logger.info(f"Server: {data.decode().strip()}")
        
        # Send file size
        sock.send(f"{file_size}".encode())
        
        # Wait for acknowledgment
        data = sock.recv(1024)
        logger.info(f"Server: {data.decode().strip()}")
        
        # Send file data
        sent = 0
        with open(filename, 'rb') as f:
            while sent < file_size:
                chunk = f.read(4096)
                if not chunk:
                    break
                sock.send(chunk)
                sent += len(chunk)
                # Print progress
                percent = int(sent * 100 / file_size)
                print(f"\rSending: {percent}% ({sent}/{file_size} bytes)", end='')
        
        print()  # New line after progress
        
        # Wait for final confirmation
        data = sock.recv(1024)
        logger.info(f"Server: {data.decode().strip()}")
        
        sock.close()
        return True
    except Exception as e:
        logger.error(f"File sending failed: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description="Reverse SSH Tunnel Experiment")
    parser.add_argument("--jump-server", required=True, help="Jump server hostname or IP")
    parser.add_argument("--jump-user", required=True, help="Username for the jump server")
    parser.add_argument("--jump-port", type=int, default=22, help="SSH port on jump server (default: 22)")
    parser.add_argument("--identity-file", help="SSH identity file (private key)")
    parser.add_argument("--use-password", action="store_true", help="Use password auth instead of key")
    parser.add_argument("--remote-port", type=int, default=8022, help="Remote port on jump server (default: 8022)")
    parser.add_argument("--local-port", type=int, default=9022, help="Local port to forward from (default: same as remote)")
    parser.add_argument("--run-server", action="store_true", help="Run a file server on local port")
    parser.add_argument("--send-file", help="Send a file through established tunnel (requires tunnel to be established)")
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
    
    if args.send_file:
        # Just send a file through an existing tunnel
        logger.info(f"Sending file {args.send_file} through tunnel to {args.jump_server}:{args.remote_port}")
        if send_file_via_tunnel(args.jump_server, args.remote_port, args.send_file):
            logger.info("File sent successfully")
        else:
            logger.error("Failed to send file")
        return
    
    # If running the server is requested, start it in a separate thread
    server_thread = None
    if args.run_server:
        local_port = args.local_port if args.local_port else args.remote_port
        logger.info(f"Starting file server on port {local_port}")
        server_thread = threading.Thread(
            target=run_file_server,
            args=(local_port,),
            daemon=True
        )
        server_thread.start()
        time.sleep(1)  # Give the server time to start
    
    # Create and establish SSH tunnel
    local_port = args.local_port if args.local_port else args.remote_port
    tunnel = SSHTunnelReverse(
        ssh_config=ssh_config,
        remote_port=args.remote_port,
        local_port=local_port
    )
    
    logger.info(f"Establishing reverse tunnel: localhost:{local_port} <- {args.jump_server}:{args.remote_port}")
    if tunnel.establish_tunnel():
        logger.info("Tunnel established successfully")
        logger.info(f"To test: Connect to {args.jump_server}:{args.remote_port} from another machine")
        
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
