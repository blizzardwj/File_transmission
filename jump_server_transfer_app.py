#!/usr/bin/env python3

import argparse
import sys
import threading
import os
import time
from jump_server_transfer import NamedPipeSSHBridge

def main():
    # Create argument parser
    parser = argparse.ArgumentParser(description="File transfer via jump server")
    
    # Add command line arguments
    parser.add_argument("--hostname", default="45.145.74.109", help="Jump server hostname")
    parser.add_argument("--port", type=int, default=5222, help="SSH port")
    parser.add_argument("--username", default="root", help="SSH username")
    parser.add_argument("--password", help="SSH password")
    parser.add_argument("--local-path", help="Local file/folder path")
    parser.add_argument("--remote-target", default="file_transfer_default", help="Remote identifier")
    parser.add_argument("--operation", choices=["send", "receive"], required=True, help="Operation type: send or receive")
    parser.add_argument("--buffer-size", type=int, default=4096 * 2, help="Buffer size in bytes (default: 4096)")
    
    # Parse arguments
    args = parser.parse_args()
    
    # If password wasn't provided as argument, prompt for it securely
    if not args.password:
        import getpass
        args.password = getpass.getpass("Enter SSH password: ")

    # prompt for local path
    if not args.local_path:
        args.local_path = input("Enter the local file/folder path: ")

    # prompt for operation
    args.operation = input("Enter the operation (send/receive): ")
    if args.operation not in ['send', 'receive']:
        print("Invalid operation. Use 'send' or 'receive'.")
        sys.exit(1)
    
    # Get parameters
    jump_host = args.hostname
    port = args.port
    user = args.username
    pwd = args.password
    op = args.operation
    local = args.local_path
    target = args.remote_target
    buffer_size = args.buffer_size

    try:
        # Create StreamBridge instance
        stream_bridge = NamedPipeSSHBridge(jump_host, port, user, pwd)
        
        # Connect to jump server
        if not stream_bridge.connect():
            print("Cannot connect to jump server")
            return
        
        try:
            # Customize pipe name based on target identifier for multiple transfers
            pipe_name = f"transfer_pipe_{target}"
            stream_bridge.PIPE_NAME = pipe_name
            
            if op == 'send':
                if not os.path.exists(local):
                    print("Error: Local path doesn't exist")
                    return
                
                print(f"Transfer pipe established, identifier: {target}")
                print(f"Starting file send using buffer size: {buffer_size} bytes...")
                
                # Use the public method which handles pipe creation internally
                if stream_bridge.send_file(local, buffer_size):
                    print("File sent successfully!")
                else:
                    print("File sending failed!")

                # # Start a asynchronous thread to wait for receiver
                # send_thread = stream_bridge.send_file_async(local)
                # if send_thread:
                #     print("Waiting for receiver to connect...")
                #     send_thread.join()
                #     print("File sent successfully!")

            elif op == 'receive':
                print(f"Beginning to receive data for identifier: {target}")
                print(f"Starting data reception using buffer size: {buffer_size} bytes...")
                if stream_bridge.receive_file(local, buffer_size):
                    print("Transfer complete!")
                else:
                    print("File reception failed!")
        finally:
            # Ensure connection is closed
            stream_bridge.close()
            
    except Exception as e:
        print(f"Error: {str(e)}")
        
if __name__ == "__main__":
    main()