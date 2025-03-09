import paramiko
import os
import threading
import ipywidgets as widgets
from IPython.display import display
from pathlib import Path
import time
import abc
import socket
import subprocess
from enum import Enum
import select
import struct

class TransferProtocol:
    """Handles file transfer protocol formatting and parsing"""
    
    HEADER_FORMAT = "!QI"  # Q: unsigned long long (8 bytes) for file size, I: unsigned int (4 bytes) for flags
    HEADER_SIZE = struct.calcsize(HEADER_FORMAT)
    
    @staticmethod
    def create_header(file_size: int, flags: int = 0) -> bytes:
        """Create protocol header"""
        return struct.pack(TransferProtocol.HEADER_FORMAT, file_size, flags)
    
    @staticmethod
    def parse_header(header: bytes) -> tuple[int, int]:
        """Parse protocol header"""
        return struct.unpack(TransferProtocol.HEADER_FORMAT, header)
        
    @staticmethod
    def create_header_from_size(file_size: int, flags: int = 0) -> bytes:
        """Create protocol header from file size"""
        return struct.pack(TransferProtocol.HEADER_FORMAT, file_size, flags)

# Pipeline communication parameters
PIPE_NAME = "transfer_pipe"
BUFFER_SIZE = 4096
LOCK = threading.Lock()

class TransferMethod(Enum):
    """Enum for available file transfer methods"""
    SSH_NAMED_PIPE = "SSH Tunnel + Named Pipes"
    NETCAT_TUNNEL = "Netcat Tunnel"
    SCP_PORT_FORWARD = "Port Forwarding with SCP"

class FileTransferBridge(abc.ABC):
    """Abstract base class for file transfer methods"""
    
    @abc.abstractmethod
    def connect(self):
        """Connect to the jump server"""
        pass
    
    @abc.abstractmethod
    def close(self):
        """Close the connection"""
        pass
    
    @abc.abstractmethod
    def send_file(self, local_file, buffer_size=BUFFER_SIZE, progress_callback=None):
        """Send a file through the bridge
        
        Args:
            local_file: Path to the file to send
            buffer_size: Size of data chunks for streaming
            progress_callback: Optional callback function for progress updates
        """
        pass
    
    @abc.abstractmethod
    def receive_file(self, save_path, buffer_size=BUFFER_SIZE, progress_callback=None):
        """Receive a file through the bridge
        
        Args:
            save_path: Path where received file will be saved
            buffer_size: Size of data chunks for streaming
            progress_callback: Optional callback function for progress updates
        """
        pass
    
    @abc.abstractmethod
    def send_file_async(self, local_file, buffer_size=BUFFER_SIZE, progress_callback=None):
        """Asynchronously send a file
        
        Args:
            local_file: Path to the file to send
            buffer_size: Size of data chunks for streaming
            progress_callback: Optional callback function for progress updates
        """
        pass

class NamedPipeSSHBridge(FileTransferBridge):
    """Bridge implementation using SSH tunneling with named pipes for file transfer.
    
    This class creates a secure file transfer mechanism using SSH tunneling combined with
    named pipes (FIFOs) on the jump server. This approach allows for:
    - Memory-efficient streaming transfer (data isn't fully loaded into memory)
    - No permanent storage on the jump server (data only exists in the pipe)
    - Bidirectional transfer capability (can send and receive)
    - Asynchronous operation support
    
    The transfer process:
    1. Creates a named pipe on the jump server
    2. Streams data through the SSH tunnel into/from the pipe
    3. Cleans up the pipe after transfer is complete
    """
    
    def __init__(self, jump_host, port, user, pwd):
        """Initialize NamedPipeSSHBridge instance with connection parameters"""
        self.jump_host = jump_host
        self.port = port
        self.user = user
        self.pwd = pwd
        self.transport = None
        self.ssh = None
        self.PIPE_NAME = PIPE_NAME
        
    def connect(self):
        """Establish SSH connection to the jump server
        
        Creates and configures both low-level transport and high-level SSH client
        for subsequent operations. The transport layer handles the secure connection,
        while the SSH client provides a convenient interface for command execution.
        
        Returns:
            bool: True if connection successful, False otherwise
        """
        try:
            self.transport = paramiko.Transport((self.jump_host, self.port))
            self.transport.connect(username=self.user, password=self.pwd)
            self.ssh = paramiko.SSHClient()
            self.ssh._transport = self.transport
            self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            return True
        except Exception as e:
            print(f"Connection error: {str(e)}")
            return False
    
    def close(self):
        """Close the connection"""
        if self.transport:
            self.transport.close()
    
    def create_pipe(self):
        """Create a named pipe (FIFO) on the jump server
        
        Executes remote commands to:
        1. Remove any existing pipe with the same name
        2. Create a new named pipe using mkfifo
        
        The pipe serves as a temporary data conduit that exists only in memory,
        allowing for streaming transfer without disk storage.
        
        Returns:
            bool: True if pipe creation successful, False otherwise
        """
        try:
            self.ssh.exec_command(f"rm -f {self.PIPE_NAME}; mkfifo {self.PIPE_NAME}")
            return True
        except Exception as e:
            print(f"Create pipe error: {str(e)}")
            return False
    
    def remove_pipe(self):
        """Remove the named pipe from the jump server
        
        Cleanup operation to ensure no residual pipes remain after transfer.
        This prevents potential conflicts in subsequent transfers and maintains
        server cleanliness.
        
        Returns:
            bool: True if pipe removal successful, False otherwise
        """
        try:
            self.ssh.exec_command(f"rm -f {self.PIPE_NAME}")
            return True
        except Exception as e:
            print(f"Remove pipe error: {str(e)}")
            return False
    
    def send_file(self, local_file, buffer_size=BUFFER_SIZE, progress_callback=None):
        """Send a file through the SSH tunnel and named pipe
        
        Process:
        1. Verifies local file existence
        2. Creates named pipe on jump server
        3. Initiates streaming transfer through SSH tunnel with progress tracking
        4. Handles the transfer in chunks for memory efficiency
        
        Args:
            local_file: Path to the file to be sent
            buffer_size: Size of data chunks for streaming
            progress_callback: Optional callback function for progress updates
            
        Returns:
            bool: True if transfer successful, False otherwise
        """
        if not os.path.exists(local_file):
            print(f"Error: local file does not exist - {local_file}")
            return False
            
        try:
            # Create pipe
            self.create_pipe()
            
            # Start writer thread
            return self._stream_writer(local_file, buffer_size, progress_callback)
        except Exception as e:
            print(f"File sending error: {str(e)}")
            return False
    
    def receive_file(self, save_path, buffer_size=BUFFER_SIZE, progress_callback=None):
        """Receive a file through the SSH tunnel and named pipe
        
        Process:
        1. Creates target directory if needed
        2. Starts reader thread to receive data
        3. Reads file header to get size information
        4. Streams data from pipe to local file with progress tracking
        5. Cleans up pipe after transfer
        
        Args:
            save_path: Path where received file will be saved
            buffer_size: Size of data chunks for streaming
            progress_callback: Optional callback function for progress updates
            
        Returns:
            bool: True if reception successful, False otherwise
        """
        try:
            # Start reader thread
            thread = threading.Thread(
                target=self._stream_reader,
                args=(save_path, buffer_size, progress_callback)
            )
            thread.start()
            thread.join()
            
            # Clean up the pipe
            self.remove_pipe()
            return True
        except Exception as e:
            print(f"Error receiving file: {str(e)}")
            return False

    def send_file_async(self, local_file, buffer_size=BUFFER_SIZE, progress_callback=None):
        """Asynchronously send a file through the SSH tunnel
        
        Creates a separate thread for the file transfer operation,
        allowing the main program to continue execution without waiting
        for transfer completion.
        
        Args:
            local_file: Path to the file to be sent
            buffer_size: Size of data chunks for streaming
            progress_callback: Optional callback function for progress updates
            
        Returns:
            Thread: Transfer thread if successful, None if failed
        """
        if not os.path.exists(local_file):
            print(f"Error: Local file doesn't exist - {local_file}")
            return False
            
        try:
            # Create pipe
            self.create_pipe()
            
            # Start writer thread
            thread = threading.Thread(
                target=self._stream_writer,
                args=(local_file, buffer_size, progress_callback)
            )
            thread.start()
            return thread
        except Exception as e:
            print(f"Send file error: {str(e)}")
            return None
    
    def _stream_writer(self, local_file, buffer_size=BUFFER_SIZE, progress_callback=None):
        """Internal method to handle continuous writing to the named pipe
        
        Process:
        1. Opens SSH channel to write to the pipe
        2. Sends protocol header with file size
        3. Reads and streams file content in chunks
        4. Reports progress through callback
        5. Handles proper channel shutdown after transfer
        
        Args:
            local_file: Path to the file being sent
            buffer_size: Size of data chunks for streaming
            progress_callback: Optional callback function for progress updates
            
        Returns:
            bool: True if streaming successful, False otherwise
        """
        try:
            # Create SSH connection to write to the pipe
            stdin, stdout, stderr = self.ssh.exec_command(
                f"cat > {self.PIPE_NAME}", 
                bufsize=0, 
                timeout=None, 
                get_pty=False
            )
            
            # Open file first to get its size and create header
            with open(local_file, 'rb') as f:
                # Get file size without extra I/O operation
                f.seek(0, os.SEEK_END)
                total_size = f.tell()
                f.seek(0)
                
                # Create and send header
                header = TransferProtocol.create_header_from_size(total_size)
                stdin.write(header)
                stdin.flush()
                
                # Read and send file content
                bytes_sent = 0
                while True:
                    chunk = f.read(buffer_size)
                    if not chunk:
                        break
                    
                    # Write data chunk to the pipe
                    stdin.write(chunk)
                    stdin.flush()
                    
                    # Update progress
                    bytes_sent += len(chunk)
                    progress = (bytes_sent / total_size) * 100
                    if progress_callback:
                        progress_callback(progress)
                    else:
                        print(f"\rProgress: {progress:.1f}%", end="", flush=True)
            
            # Close the write channel
            stdin.channel.shutdown_write()
            
            # Check for errors
            err = stderr.read().decode().strip()
            if err:
                print(f"\nWrite error: {err}")
                return False
            
            print("\nFile transfer complete")
            return True
        except Exception as e:
            print(f"Data transmission error: {str(e)}")
            return False
    
    def _stream_reader(self, save_path, buffer_size=BUFFER_SIZE, progress_callback=None):
        """Internal method to handle continuous reading from the named pipe
        
        Process:
        1. Ensures target directory exists
        2. Opens SSH channel to read from pipe
        3. Reads protocol header to get file size
        4. Streams data chunks from pipe to local file
        5. Reports progress through callback
        6. Handles proper file flushing and error checking
        
        Args:
            save_path: Path where received file will be saved
            buffer_size: Size of data chunks for streaming
            progress_callback: Optional callback function for progress updates
            
        Returns:
            bool: True if streaming successful, False otherwise
        """
        try:
            # Ensure the target directory exists
            Path(save_path).parent.mkdir(parents=True, exist_ok=True)
            
            # Wait for the pipe to be ready
            time.sleep(1)
            
            # Open pipe for reading
            stdin, stdout, stderr = self.ssh.exec_command(
                f"cat {self.PIPE_NAME}", 
                bufsize=buffer_size,
                timeout=None
            )
            
            # First read the protocol header
            header = stdout.read(TransferProtocol.HEADER_SIZE)
            if len(header) != TransferProtocol.HEADER_SIZE:
                print("\nError: Failed to read protocol header")
                return False
                
            total_size, flags = TransferProtocol.parse_header(header)
            
            # Read and write file data with progress tracking
            with open(save_path, 'wb') as f:
                bytes_received = 0
                while bytes_received < total_size:
                    # Calculate remaining bytes to read
                    remaining = total_size - bytes_received
                    chunk_size = min(buffer_size, remaining)
                    
                    data = stdout.read(chunk_size)
                    if not data:
                        print("\nError: Connection closed before receiving complete file")
                        return False
                        
                    f.write(data)
                    f.flush()
                    
                    # Update progress
                    bytes_received += len(data)
                    progress = (bytes_received / total_size) * 100
                    if progress_callback:
                        progress_callback(progress)
                    else:
                        print(f"\rProgress: {progress:.1f}%", end="", flush=True)
                
            # Check for errors
            err = stderr.read().decode().strip()
            if err:
                print(f"\nRead error: {err}")
                return False
                
            print("\nFile reception complete")
            return True
        except Exception as e:
            print(f"Data reception error: {str(e)}")
            return False

class NetcatTunnelBridge(FileTransferBridge):
    """File transfer implementation using SSH tunneling with Netcat relay.
    
    This class provides a file transfer mechanism that combines SSH tunneling
    with Netcat (nc) for data relay. Key features:
    - Zero storage on jump server (pure memory streaming)
    - Direct TCP/IP tunneling through SSH
    - Efficient network socket-based transfer
    - No temporary files or pipes needed
    
    The transfer process:
    1. Establishes SSH connection to jump server
    2. Creates Netcat listener for data relay
    3. Sets up direct-tcpip channel through SSH
    4. Streams data through the tunnel
    5. Cleans up connections after transfer
    """
    
    def __init__(self, jump_host, port, user, pwd):
        """Initialize NetcatTunnelBridge with connection parameters
        
        Args:
            jump_host: Hostname/IP of the jump server
            port: SSH port number
            user: SSH username
            pwd: SSH password
        """
        self.jump_host = jump_host
        self.port = port
        self.user = user
        self.pwd = pwd
        self.ssh = None
        
    def connect(self):
        """Connect to the jump server"""
        try:
            self.ssh = paramiko.SSHClient()
            self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            self.ssh.connect(
                hostname=self.jump_host,
                port=self.port,
                username=self.user,
                password=self.pwd
            )
            return True
        except Exception as e:
            print(f"Connection error: {str(e)}")
            return False
    
    def close(self):
        """Close the connection"""
        if self.ssh:
            self.ssh.close()
    
    def send_file(self, local_file, buffer_size=BUFFER_SIZE):
        """Send file through SSH tunnel using Netcat relay
        
        Process:
        1. Verifies local file existence
        2. Sets up Netcat listener on jump server
        3. Creates direct-tcpip channel to the listener
        4. Streams file data through the tunnel
        5. Handles proper cleanup of channels
        
        The use of Netcat allows for efficient streaming without
        any intermediate storage on the jump server.
        
        Args:
            local_file: Path to the file to be sent
            buffer_size: Size of data chunks for streaming
            
        Returns:
            bool: True if transfer successful, False otherwise
        """
        if not os.path.exists(local_file):
            print(f"Error: local file does not exist - {local_file}")
            return False
        
        try:
            # Create a direct tunnel through the jump server
            transport = self.ssh.get_transport()
            
            # Create a channel for executing remote command
            channel = transport.open_session()
            
            # Start a command on the jump server that will relay data without storing it
            # This command reads from stdin and writes to a network socket without disk storage
            relay_command = "nc -l 12345 > /dev/null 2>&1 &"
            channel.exec_command(relay_command)
            
            # Wait a moment for the netcat listener to start
            time.sleep(1)
            
            # Create a second channel for direct-tcpip to connect to the netcat listener
            dest_addr = ('localhost', 12345)  # Destination on the jump server
            src_addr = ('localhost', 0)       # Source on local side
            
            # Open the channel
            forward_channel = transport.open_channel("direct-tcpip", dest_addr, src_addr)
            
            # Read from local file and send through the tunnel
            with open(local_file, 'rb') as f:
                while True:
                    data = f.read(buffer_size)
                    if not data:
                        break
                    forward_channel.send(data)
            
            # Close the channels
            forward_channel.close()
            channel.close()
            
            print("File transfer complete")
            return True
        except Exception as e:
            print(f"File sending error: {str(e)}")
            return False
    
    def receive_file(self, save_path, buffer_size=BUFFER_SIZE):
        """Receive file through SSH tunnel using Netcat relay
        
        Process:
        1. Creates target directory if needed
        2. Sets up Netcat listener for receiving
        3. Establishes direct-tcpip channel
        4. Streams data from tunnel to local file
        5. Ensures proper channel cleanup
        
        Uses Netcat's built-in streaming capabilities to receive
        data efficiently without temporary storage.
        
        Args:
            save_path: Path where received file will be saved
            buffer_size: Size of data chunks for streaming
            
        Returns:
            bool: True if reception successful, False otherwise
        """
        try:
            # Ensure the target directory exists
            Path(save_path).parent.mkdir(parents=True, exist_ok=True)
            
            # Create a direct tunnel through the jump server
            transport = self.ssh.get_transport()
            
            # Create a channel for executing remote command
            channel = transport.open_session()
            
            # Start a command on the jump server that will relay data without storing it
            # This command reads from a network socket and writes to stdout without disk storage
            relay_command = "nc -l 12345"
            channel.exec_command(relay_command)
            
            # Wait a moment for the netcat listener to start
            time.sleep(1)
            
            # Create a second channel for direct-tcpip to connect to the netcat listener
            dest_addr = ('localhost', 12345)  # Destination on the jump server
            src_addr = ('localhost', 0)       # Source on local side
            
            # Open the channel
            forward_channel = transport.open_channel("direct-tcpip", dest_addr, src_addr)
            
            # Read from the tunnel and write to local file
            with open(save_path, 'wb') as f:
                while True:
                    data = channel.recv(buffer_size)
                    if not data:
                        break
                    f.write(data)
            
            # Close the channels
            forward_channel.close()
            channel.close()
            
            print("File reception complete")
            return True
        except Exception as e:
            print(f"Error receiving file: {str(e)}")
            return False

class SCPPortForwardBridge(FileTransferBridge):
    """Implementation of file transfer using Port Forwarding with SCP"""
    
    def __init__(self, jump_host, port, user, pwd):
        """Initialize SCPPortForwardBridge instance"""
        self.jump_host = jump_host
        self.port = port
        self.user = user
        self.pwd = pwd
        self.ssh = None
        self.local_port = None
        self.remote_port = None
        self.forward_thread = None
        self.stop_event = threading.Event()
        
    def connect(self):
        """Connect to the jump server and establish port forwarding"""
        try:
            # Connect to jump server
            self.ssh = paramiko.SSHClient()
            self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            self.ssh.connect(
                hostname=self.jump_host,
                port=self.port,
                username=self.user,
                password=self.pwd
            )
            return True
        except Exception as e:
            print(f"Connection error: {str(e)}")
            return False
    
    def close(self):
        """Close the connection and stop port forwarding"""
        if self.forward_thread and self.forward_thread.is_alive():
            self.stop_event.set()
            self.forward_thread.join(timeout=5)
            
        if self.ssh:
            self.ssh.close()
    
    def _find_free_port(self):
        """Find a free port on the local machine"""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('', 0))
            return s.getsockname()[1]
    
    def _setup_port_forwarding(self, remote_host, remote_port):
        """Set up port forwarding from local to remote through jump server"""
        # Find a free local port
        self.local_port = self._find_free_port()
        self.remote_port = remote_port
        
        # Start port forwarding in a separate thread
        transport = self.ssh.get_transport()
        self.stop_event.clear()
        
        def forward_tunnel():
            try:
                transport.request_port_forward('', self.local_port)
                while not self.stop_event.is_set():
                    chan = transport.accept(1)
                    if chan is None:
                        continue
                    
                    thr = threading.Thread(
                        target=self._handler,
                        args=(chan, remote_host, remote_port)
                    )
                    thr.daemon = True
                    thr.start()
            except Exception as e:
                if not self.stop_event.is_set():
                    print(f"Port forwarding error: {str(e)}")
            finally:
                try:
                    transport.cancel_port_forward('', self.local_port)
                except:
                    pass
        
        self.forward_thread = threading.Thread(target=forward_tunnel)
        self.forward_thread.daemon = True
        self.forward_thread.start()
        
        # Give time for port forwarding to establish
        time.sleep(1)
        return self.local_port
    
    def _handler(self, chan, host, port):
        """Handle the port forwarding connection"""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect((host, port))
            
            # Bidirectional forwarding
            while True:
                r, w, x = select.select([sock, chan], [], [])
                if sock in r:
                    data = sock.recv(BUFFER_SIZE)
                    if len(data) == 0:
                        break
                    chan.send(data)
                if chan in r:
                    data = chan.recv(BUFFER_SIZE)
                    if len(data) == 0:
                        break
                    sock.send(data)
        except Exception as e:
            print(f"Forwarding handler error: {str(e)}")
        finally:
            try:
                sock.close()
                chan.close()
            except:
                pass
    
    def send_file(self, local_file, buffer_size=BUFFER_SIZE):
        """Send file using SCP through port forwarding"""
        if not os.path.exists(local_file):
            print(f"Error: local file does not exist - {local_file}")
            return False
        
        try:
            # Set up direct port forwarding to the remote host
            # For sending, we're forwarding to localhost on the jump server
            local_port = self._setup_port_forwarding('localhost', 22)
            
            # Use subprocess to run scp command
            cmd = [
                'scp',
                '-P', str(local_port),
                '-o', 'StrictHostKeyChecking=no',
                '-o', 'UserKnownHostsFile=/dev/null',
                local_file,
                f'{self.user}@localhost:/tmp/scp_transfer'
            ]
            
            # Execute SCP command
            process = subprocess.Popen(
                cmd, 
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            stdout, stderr = process.communicate()
            
            if process.returncode != 0:
                print(f"SCP error: {stderr.decode()}")
                return False
                
            print("File transfer complete")
            return True
        except Exception as e:
            print(f"File sending error: {str(e)}")
            return False
        finally:
            # Stop port forwarding
            self.stop_event.set()
    
    def receive_file(self, save_path, buffer_size=BUFFER_SIZE):
        """Receive file using SCP through port forwarding"""
        try:
            # Ensure the target directory exists
            Path(save_path).parent.mkdir(parents=True, exist_ok=True)
            
            # Set up direct port forwarding to the remote host
            local_port = self._setup_port_forwarding('localhost', 22)
            
            # Use subprocess to run scp command
            cmd = [
                'scp',
                '-P', str(local_port),
                '-o', 'StrictHostKeyChecking=no',
                '-o', 'UserKnownHostsFile=/dev/null',
                f'{self.user}@localhost:/tmp/scp_transfer',
                save_path
            ]
            
            # Execute SCP command
            process = subprocess.Popen(
                cmd, 
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            stdout, stderr = process.communicate()
            
            if process.returncode != 0:
                print(f"SCP error: {stderr.decode()}")
                return False
                
            print("File reception complete")
            return True
        except Exception as e:
            print(f"Error receiving file: {str(e)}")
            return False
        finally:
            # Stop port forwarding
            self.stop_event.set()
    
    def send_file_async(self, local_file, buffer_size=BUFFER_SIZE):
        """Asynchronously send a file using SCP through port forwarding"""
        if not os.path.exists(local_file):
            print(f"Error: Local file doesn't exist - {local_file}")
            return False
            
        try:
            # Start sender thread
            thread = threading.Thread(
                target=self.send_file,
                args=(local_file, buffer_size)
            )
            thread.start()
            return thread
        except Exception as e:
            print(f"Send file error: {str(e)}")
            return None

class FileTransferFactory:
    """Factory class to create appropriate file transfer bridge instances"""
    
    @staticmethod
    def create_bridge(method, jump_host, port, user, pwd):
        """Create and return a file transfer bridge instance based on the specified method"""
        if method == TransferMethod.SSH_NAMED_PIPE:
            return NamedPipeSSHBridge(jump_host, port, user, pwd)
        elif method == TransferMethod.SCP_PORT_FORWARD:
            return SCPPortForwardBridge(jump_host, port, user, pwd)
        else:
            raise ValueError(f"Unsupported transfer method: {method}")

# # The following part is for jupyter notebook
# # Component definitions
# jump_ip = widgets.Text(description="Jump server IP:")
# jump_port = widgets.IntText(value=22, description="SSH port:")
# username = widgets.Text(description="username:")
# password = widgets.Password(description="password:")
# transfer_method = widgets.Dropdown(
#     options=[(method.value, method) for method in TransferMethod],
#     description='Method:'
# )
# operation = widgets.Dropdown(
#     options=['send', 'receive'],
#     description='Operation:'
# )
# local_path = widgets.Text(description="Local path:")
# remote_target = widgets.Text(description="Remote identifier:")
# submit = widgets.Button(description="Start transfer")

# def on_submit(b):
#     """Handle submit button click"""
#     # Get parameters
#     jump_server = jump_ip.value
#     port = jump_port.value
#     user = username.value
#     pwd = password.value
#     op = operation.value
#     path = local_path.value
#     target = remote_target.value
#     method = transfer_method.value
    
#     if not all([jump_server, user, pwd, path, target]):
#         print("Please fill in all required fields")
#         return
    
#     # Create appropriate bridge instance
#     bridge = FileTransferFactory.create_bridge(method, jump_server, port, user, pwd)
    
#     # Connect to jump server
#     print(f"Connecting to {jump_server}...")
#     if not bridge.connect():
#         print("Connection failed")
#         return
    
#     try:
#         # Perform file transfer
#         if op == 'send':
#             print(f"Sending file {path} to {target}...")
#             success = bridge.send_file(path)
#         else:  # receive
#             print(f"Receiving file from {target} to {path}...")
#             success = bridge.receive_file(path)
        
#         if success:
#             print("Operation completed successfully")
#         else:
#             print("Operation failed")
#     finally:
#         # Close connection
#         bridge.close()

# # Bind event
# submit.on_click(on_submit)

# # Display interface
# display(widgets.VBox([
#     widgets.HBox([jump_ip, jump_port]),
#     widgets.HBox([username, password]),
#     widgets.HBox([transfer_method, operation]),
#     widgets.HBox([local_path, remote_target]),
#     submit
# ]))
