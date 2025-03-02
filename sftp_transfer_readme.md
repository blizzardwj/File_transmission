# SFTP Transfer Tool - Usage Guide

This document explains how to use the SFTP-based transfer tool in the `file_transmission_b.ipynb` notebook.

## How It Works

Unlike the other transfer methods that require coordination between sender and receiver, the SFTP method handles the complete transfer process in a single operation:

1. The tool establishes an SSH connection to the relay server
2. It uses the SFTP protocol (built on SSH) to transfer files securely
3. Progress is tracked with real-time updates

## Usage Instructions

### For Sending Files TO Remote Server:

1. Fill in the connection details:
   - Jump server IP address
   - SSH port (usually 22)
   - Username and password

2. Set the paths:
   - **Local path**: The file or folder on your machine you want to send
   - **Remote path**: The destination path on the remote server

3. Select operation type: **send**

4. Click "执行传输" to start the transfer

### For Receiving Files FROM Remote Server:

1. Fill in the same connection details as above

2. Set the paths:
   - **Local path**: Where you want to save the file/folder on your machine
   - **Remote path**: The file or folder on the remote server you want to download

3. Select operation type: **receive**

4. Click "执行传输" to start the transfer

## Key Features

- **Resume Support**: If a transfer is interrupted, it can be resumed from where it left off
- **Progress Tracking**: Real-time progress bars show transfer speed and completion percentage
- **Directory Support**: Can transfer entire directories with nested folder structures
- **Secure Transfer**: All data is encrypted during transmission

## Troubleshooting

- **Connection Issues**: Verify the server IP, port, and credentials are correct
- **Permission Errors**: Ensure you have read/write permissions for the specified paths
- **Path Format**: Use absolute paths for best results (e.g., `/home/user/files` rather than `~/files`)

## Advantages Over Basic Transfer Methods

This SFTP-based approach is superior to the netcat methods because:
1. No coordination needed between sender and receiver
2. Built-in encryption for secure transfers
3. Resume capability for interrupted transfers
4. Automatic directory structure preservation
