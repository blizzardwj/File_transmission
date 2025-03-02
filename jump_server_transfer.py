import paramiko
import os
import threading
import ipywidgets as widgets
from IPython.display import display
from pathlib import Path
import time

# Pipeline communication parameters
PIPE_NAME = "transfer_pipe"
BUFFER_SIZE = 4096
LOCK = threading.Lock()

class StreamBridge:
    def __init__(self, jump_host, port, user, pwd):
        """Initialize StreamBridge instance"""
        self.jump_host = jump_host
        self.port = port
        self.user = user
        self.pwd = pwd
        self.transport = None
        self.ssh = None
        self.PIPE_NAME = PIPE_NAME
        
    def connect(self):
        """Connect to the jump server"""
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
        """Create a named pipe in memory on the jump server"""
        try:
            self.ssh.exec_command(f"rm -f {self.PIPE_NAME}; mkfifo {self.PIPE_NAME}")
            return True
        except Exception as e:
            print(f"Create pipe error: {str(e)}")
            return False
    
    def remove_pipe(self):
        """Clean up the pipe"""
        try:
            self.ssh.exec_command(f"rm -f {self.PIPE_NAME}")
            return True
        except Exception as e:
            print(f"Remove pipe error: {str(e)}")
            return False
    
    def send_file(self, local_file, buffer_size=BUFFER_SIZE):
        """Sender sends file"""
        if not os.path.exists(local_file):
            print(f"Error: local file does not exist - {local_file}")
            return False
            
        try:
            # Create pipe
            self.create_pipe()
            
            # Start writer thread
            return self._stream_writer(local_file, buffer_size)
        except Exception as e:
            print(f"File sending error: {str(e)}")
            return False
    
    def receive_file(self, save_path, buffer_size=BUFFER_SIZE):
        """Receiver receives file"""
        try:
            # Start reader thread
            thread = threading.Thread(
                target=self._stream_reader,
                args=(save_path, buffer_size)
            )
            thread.start()
            thread.join()
            
            # Clean up the pipe
            self.remove_pipe()
            return True
        except Exception as e:
            print(f"Error receiving file: {str(e)}")
            return False

    # Add a new method to StreamBridge class
    def send_file_async(self, local_file, buffer_size=BUFFER_SIZE):
        """Asynchronously send a file"""
        if not os.path.exists(local_file):
            print(f"Error: Local file doesn't exist - {local_file}")
            return False
            
        try:
            # Create pipe
            self.create_pipe()
            
            # Start writer thread
            thread = threading.Thread(
                target=self._stream_writer,
                args=(local_file, buffer_size)
            )
            thread.start()
            return thread
        except Exception as e:
            print(f"Send file error: {str(e)}")
            return None
    
    def _stream_writer(self, local_file, buffer_size=BUFFER_SIZE):
        """Sender continuously writes to the pipe"""
        try:
            # First create a single SSH connection to write to the pipe
            stdin, stdout, stderr = self.ssh.exec_command(
                f"cat > {self.PIPE_NAME}", 
                bufsize=0, 
                timeout=None, 
                get_pty=False
            )
            
            # Read and send file content
            with open(local_file, 'rb') as f:
                while True:
                    chunk = f.read(buffer_size)
                    if not chunk:
                        break
                    
                    # Write data chunk to the pipe
                    stdin.write(chunk)
                    stdin.flush()
            
            # Close the write channel
            stdin.channel.shutdown_write()
            
            # Check for errors
            err = stderr.read().decode().strip()
            if err:
                print(f"Write error: {err}")
                
            print("File transfer complete")
            return True
        except Exception as e:
            print(f"Data transmission error: {str(e)}")
            return False
    
    def _stream_reader(self, save_path, buffer_size=BUFFER_SIZE):
        """Receiver continuously reads from the pipe"""
        try:
            # Ensure the target directory exists
            Path(save_path).parent.mkdir(parents=True, exist_ok=True)
            
            # Wait for the pipe to be ready
            time.sleep(1)
            
            with open(save_path, 'wb') as f:
                # Read data from the pipe
                stdin, stdout, stderr = self.ssh.exec_command(
                    f"cat {self.PIPE_NAME}", 
                    bufsize=buffer_size,
                    timeout=None
                )
                
                # Read and write data
                while True:
                    data = stdout.read(buffer_size)
                    if not data:
                        break
                    f.write(data)
                    f.flush()
                    
                # Check for errors
                err = stderr.read().decode().strip()
                if err:
                    print(f"Read error: {err}")
                    
            print("File reception complete")
            return True
        except Exception as e:
            print(f"Data reception error: {str(e)}")
            return False

# The following part is for jupyter notebook
# Component definitions
jump_ip = widgets.Text(description="Jump server IP:")
jump_port = widgets.IntText(value=22, description="SSH port:")
username = widgets.Text(description="username:")
password = widgets.Password(description="password:")
operation = widgets.Dropdown(
    options=['send', 'receive'],
    description='Operation:'
)
local_path = widgets.Text(description="Local path:")
remote_target = widgets.Text(description="Remote identifier:")
submit = widgets.Button(description="Start transfer")
status = widgets.Output()

def on_submit(b):
    with status:
        status.clear_output()
        # Get parameters
        jump_host = jump_ip.value
        port = jump_port.value
        user = username.value
        pwd = password.value
        op = operation.value
        local = local_path.value
        target = remote_target.value
        
        try:
            # Create StreamBridge instance
            stream_bridge = StreamBridge(jump_host, port, user, pwd)
            
            # Connect to the jump server
            if not stream_bridge.connect():
                print("Unable to connect to the jump server")
                return
            
            try:
                if op == 'send':
                    if not os.path.exists(local):
                        print("Error: local path does not exist")
                        return
                    
                    print(f"Transfer pipe is established, identifier: {target}")
                    print("Start sending file...")
                    stream_bridge.send_file(local)
                    print("Waiting for the receiver to connect...")

                elif op == 'receive':
                    print("Start receiving data...")
                    stream_bridge.receive_file(local)
                    print("Transmission complete!")
            finally:
                # Ensure the connection is closed
                stream_bridge.close()
                
        except Exception as e:
            print(f"Error: {str(e)}")

# Bind event
submit.on_click(on_submit)

# Display interface
display(jump_ip, jump_port, username, password, operation,
        local_path, remote_target, submit, status)