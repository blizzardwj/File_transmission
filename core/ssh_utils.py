import os
import socket
import subprocess
import time
import getpass
import shutil
from enum import Enum
from dataclasses import dataclass
from typing import Optional, List, Union, cast
import pexpect
import sys
from core.utils import build_logger

# Configure logging
logger = build_logger(__name__)

class TransferMode(Enum):
    SENDER = "sender"
    RECEIVER = "receiver"

@dataclass
class SSHConfig:
    jump_server: str
    jump_user: str
    jump_port: int = 22
    identity_file: Optional[str] = None  # SSH identity file (private key)
    use_password: bool = False  # Default to key-based authentication
    password: Optional[str] = None  # Store password if provided
    
    def get_ssh_command_base(self) -> List[str]:
        """Returns base SSH command with common options"""
        cmd = ["ssh"]
        if self.identity_file:
            cmd.extend(["-i", self.identity_file])
            cmd.extend(["-o", "PreferredAuthentications=publickey"])
        elif self.use_password:
            # Force password authentication
            cmd.extend(["-o", "PreferredAuthentications=password"])
            cmd.extend(["-o", "PubkeyAuthentication=no"])
        cmd.extend(["-p", str(self.jump_port)])
        return cmd

class SSHTunnelBase:
    """Base class for managing SSH tunnel creation and maintenance"""
    
    def __init__(self, ssh_config: SSHConfig):
        self.ssh_config = ssh_config
        self.tunnel_process: Optional[Union[subprocess.Popen, pexpect.spawn]] = None
        self.is_active = False
        self.is_pexpect = False # Initialize is_pexpect here

    def _handle_pexpect_interaction(self, cmd_str: str) -> bool:
        """Handles SSH interaction using pexpect, including host authenticity and password."""
        try:
            logger.info(f"Pexpect: Spawning command: {cmd_str}")
            
            # Increased timeout for more complex interactions, like host key checking + password
            self.tunnel_process = pexpect.spawn(cmd_str, timeout=10) 
            if sys.stdout.isatty(): # Only log to stdout if it's a TTY to avoid issues with pipes
                self.tunnel_process.logfile_read = sys.stdout.buffer
            else:
                # If not a TTY, consider logging to a dedicated file or internal buffer for debugging
                logger.info("Pexpect: Not a TTY, pexpect output will not be sent to sys.stdout.buffer")

            # Expect host authenticity, password, EOF, or TIMEOUT
            # The order matters: check for authenticity first as it can precede password prompt
            expected_patterns = [
                r"Are you sure you want to continue connecting \(yes/no/\[fingerprint\]\)\?", # Host authenticity
                r"[Pp]assword:",                             # Password prompt (case-insensitive)
                r"Host key verification failed\.",          # Explicit host key failure
                r"Permission denied, please try again\.",   # Common incorrect password message
                r"Connection refused",                     # Connection outright refused
                pexpect.EOF,
                pexpect.TIMEOUT
            ]

            # Loop to handle multi-stage prompts (e.g., authenticity then password)
            while True:
                logger.debug(f"Pexpect: Expecting one of: {expected_patterns}")
                try:
                    index = self.tunnel_process.expect(expected_patterns, timeout=10) # Timeout for each expect stage
                except pexpect.exceptions.TIMEOUT:
                    logger.error(f"Pexpect: TIMEOUT waiting for any of {expected_patterns}. Output so far: {self.tunnel_process.before}")
                    return False
                except pexpect.exceptions.EOF:
                     logger.error(f"Pexpect: EOF encountered unexpectedly during expect. Output so far: {self.tunnel_process.before}")
                     return False

                if index == 0:  # Host authenticity prompt
                    logger.info("Pexpect: Responding 'yes' to host authenticity prompt.")
                    self.tunnel_process.sendline("yes")
                    # Continue in the loop to expect the next pattern (e.g., password or EOF)
                    continue 
                elif index == 1:  # Password prompt
                    if self.ssh_config.password:
                        logger.info("Pexpect: Sending password.")
                        self.tunnel_process.sendline(self.ssh_config.password)
                        # After sending password, we expect the connection to proceed or fail (EOF/TIMEOUT or specific error message)
                        # We will break and let the isalive() check determine success.
                        break 
                    else:
                        logger.error("Pexpect: Password prompt received, but no password configured.")
                        self.tunnel_process.close(force=True)
                        return False
                elif index == 2: # Host key verification failed
                    logger.error(f"Pexpect: Host key verification failed. Output: {self.tunnel_process.before}")
                    logger.info(f"remove with: ssh-keygen -f '/home/{os.getlogin()}/.ssh/known_hosts' -R {self.ssh_config.jump_server}")
                    self.tunnel_process.close(force=True)
                    return False
                elif index == 3: # Permission denied
                    logger.error(f"Pexpect: Permission denied (e.g., wrong password). Output: {self.tunnel_process.before}")
                    # It might re-prompt for password, but for simplicity, we'll treat first denial as failure here.
                    # A more complex handler could allow retries or expect the password prompt again.
                    self.tunnel_process.close(force=True)
                    return False
                elif index == 4: # Connection refused
                    logger.error(f"Pexpect: Connection refused by remote host. Output: {self.tunnel_process.before}")
                    self.tunnel_process.close(force=True)
                    return False
                elif index == 5:  # EOF
                    # EOF might mean success if no password was needed after authenticity, or failure.
                    # The isalive() check after this loop will be the final arbiter for successful connection.
                    logger.info(f"Pexpect: EOF received. This might be normal if connection established or an early exit. Output so far: {self.tunnel_process.before}")
                    break # Exit loop, isalive() will check
                elif index == 6:  # TIMEOUT (should be caught by the inner try-except)
                    logger.error(f"Pexpect: SSH process timed out waiting for expected prompt. Output so far: {self.tunnel_process.before}")
                    self.tunnel_process.close(force=True)
                    return False
            
            self.is_pexpect = True
            return True # Indicates pexpect interaction attempted, success determined by subsequent checks

        except pexpect.exceptions.ExceptionPexpect as e:
            logger.error(f"Pexpect: An unhandled pexpect error occurred: {e}")
            if self.tunnel_process and hasattr(self.tunnel_process, 'before'): # Keep hasattr for runtime safety
                # Cast to pexpect.spawn for type checker to recognize 'before'
                pexpect_process = cast(pexpect.spawn, self.tunnel_process)
                if pexpect_process.before:
                    logger.error(f"Pexpect: Output before error: {pexpect_process.before.strip()}")
            if self.tunnel_process:
                try:
                    # Also cast here if direct close is attempted on self.tunnel_process and it's pexpect specific
                    # However, pexpect.spawn().close() is a general method name, so direct cast might not be needed for .close()
                    # For safety, ensure it's a pexpect process if using pexpect-specific close behavior
                    if isinstance(self.tunnel_process, pexpect.spawn):
                         cast(pexpect.spawn, self.tunnel_process).close(force=True)
                    elif isinstance(self.tunnel_process, subprocess.Popen):
                         # subprocess.Popen does not have a close method, use terminate/kill
                         self.tunnel_process.kill() 
                except Exception:
                    pass # Ignore errors on close if already in an error state
            return False
        except ImportError:
            logger.error("Pexpect library is not installed. Please ensure it is installed: pip install pexpect")
            return False

    def _establish_tunnel_common(self, cmd: List[str]) -> bool:
        """
        Common tunnel establishment logic
        """
        try:
            logger.info(f"Establishing SSH tunnel: {' '.join(cmd)}")
            
            # Initialize is_pexpect flag
            self.is_pexpect = False
            
            if self.ssh_config.use_password:
                # Get password interactively if not already provided
                if not self.ssh_config.password:
                    password = getpass.getpass(f"Enter SSH password for {self.ssh_config.jump_user}@{self.ssh_config.jump_server}: ")
                    self.ssh_config.password = password
                
                # Check for sshpass availability
                sshpass_available = shutil.which("sshpass")
                
                # For password auth with sshpass
                if sshpass_available:
                    logger.info("Found sshpass: using for non-interactive password authentication")
                    # Use sshpass for non-interactive password input
                    sshpass_cmd = ["sshpass", "-p", self.ssh_config.password]
                    cmd = sshpass_cmd + cmd
                    self.tunnel_process = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE
                    )
                    # Use subprocess-specific check
                    self.is_pexpect = False
                else:
                    logger.warning("sshpass not found in system PATH. Install it for better password handling, or falling back to pexpect.")
                    # Fallback to expect-like behavior if sshpass is not available
                    try:
                        # Convert list to command string
                        cmd_str = " ".join(cmd)
                        if not self._handle_pexpect_interaction(cmd_str):
                            logger.error("Pexpect interaction failed to establish connection.")
                            return False
                        # If _handle_pexpect_interaction returns True, self.is_pexpect is set.
                    except Exception as e: # Catch any unexpected error during setup for pexpect
                        logger.error(f"Error setting up pexpect interaction: {e}")
                        return False
            else:
                # For key-based auth, proceed as normal
                self.tunnel_process = subprocess.Popen(
                    cmd, 
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
                # Use subprocess-specific check
                self.is_pexpect = False
            
            # Wait for tunnel to establish
            time.sleep(2)
            
            # Check if process is still running - different methods for pexpect vs subprocess
            if self.is_pexpect:
                # For pexpect objects, check if the process is alive
                # Give a slight delay for the process to stabilize after interaction
                time.sleep(1) # Increased sleep to allow tunnel to fully establish or fail
                if not isinstance(self.tunnel_process, pexpect.spawn):
                    logger.error("Pexpect flag is set, but tunnel_process is not a pexpect.spawn object.")
                    return False

                if self.tunnel_process and self.tunnel_process.isalive():
                    self.is_active = True
                    logger.info("SSH tunnel (via pexpect) established successfully and process is alive.")
                    return True
                else:
                    logger.error(f"Failed to establish SSH tunnel with pexpect: process is not alive after interaction.")
                    # Attempt to get more details from pexpect if available
                    if self.tunnel_process and hasattr(self.tunnel_process, 'before') and self.tunnel_process.before:
                        logger.error(f"Pexpect last output before presumed failure: {self.tunnel_process.before.strip()}")
                    elif self.tunnel_process and hasattr(self.tunnel_process, 'exitstatus'): # hasattr check for safety
                        logger.error(f"Pexpect process exit status: {self.tunnel_process.exitstatus}, signal status: {self.tunnel_process.signalstatus}")
                    return False
            else:
                # For subprocess objects, use poll()
                if self.tunnel_process: # Check if tunnel_process is not None
                    if not isinstance(self.tunnel_process, subprocess.Popen):
                        logger.error("Subprocess flag is set, but tunnel_process is not a Popen object.")
                        return False
                    if self.tunnel_process.poll() is None:
                        self.is_active = True
                        logger.info("SSH tunnel established successfully")
                        return True
                    else:
                        stdout, stderr = self.tunnel_process.communicate()
                        logger.error(f"Failed to establish SSH tunnel: {stderr.decode().strip() if stderr else 'Unknown error'}")
                        return False
                else:
                    logger.error("Failed to establish SSH tunnel: tunnel_process is None for subprocess path.")
                    return False
                
        except Exception as e:
            logger.error(f"Error establishing SSH tunnel: {e}")
            return False
            
    def close_tunnel(self):
        """Close the SSH tunnel"""
        if self.tunnel_process and self.is_active:
            logger.info("Closing SSH tunnel")
            
            # Different handling based on process type
            if hasattr(self, 'is_pexpect') and self.is_pexpect:
                # For pexpect objects
                if isinstance(self.tunnel_process, pexpect.spawn):
                    try:
                        self.tunnel_process.close(force=True)  # Force kill the process
                    except Exception as e:
                        logger.warning(f"Error closing pexpect tunnel: {e}")
                else:
                    logger.warning("Attempted to close pexpect tunnel, but process is not a pexpect.spawn instance.")
            else:
                # For subprocess objects
                if isinstance(self.tunnel_process, subprocess.Popen):
                    try:
                        self.tunnel_process.terminate()
                        self.tunnel_process.wait(timeout=5)
                    except Exception as e:
                        logger.warning(f"Error closing subprocess tunnel: {e}")
                        # Force kill if terminate fails
                        try:
                            self.tunnel_process.kill()
                        except Exception as kill_e:
                            logger.warning(f"Error force killing subprocess tunnel: {kill_e}")
                else:
                    logger.warning("Attempted to close subprocess tunnel, but process is not a Popen instance.")
                        
            self.is_active = False

class SSHTunnelForward(SSHTunnelBase):
    """Manages forward SSH tunnel creation and maintenance"""
    
    def __init__(self, ssh_config: SSHConfig, local_port: int, remote_host: str, remote_port: int):
        super().__init__(ssh_config)
        self.local_port = local_port
        self.remote_host = remote_host
        self.remote_port = remote_port
        
    def establish_tunnel(self) -> bool:
        """
        Create forward SSH tunnel through the jump server
        
        Returns:
            True if successful, False otherwise
        """
        cmd = self.ssh_config.get_ssh_command_base()
        
        # Add port forwarding
        cmd.extend([
            "-L", f"{self.local_port}:{self.remote_host}:{self.remote_port}",
            # if you want other machines on your local network to access local port uncomment the following line to substitute the above line
            # "-L", f"0.0.0.0:{self.local_port}:{self.remote_host}:{self.remote_port}", 
            "-N",  # Don't execute a remote command
            f"{self.ssh_config.jump_user}@{self.ssh_config.jump_server}"
        ])
        
        return self._establish_tunnel_common(cmd)

class SSHTunnelReverse(SSHTunnelBase):
    """Manages reverse SSH tunnel creation and maintenance"""
    
    def __init__(self, ssh_config: SSHConfig, remote_port: int, local_host: str = "localhost", local_port: Optional[int] = None):
        super().__init__(ssh_config)
        self.remote_port = remote_port
        self.local_host = local_host
        self.local_port = local_port if local_port else remote_port
        
    def establish_tunnel(self) -> bool:
        """
        Create reverse SSH tunnel through the jump server
        
        Returns:
            True if successful, False otherwise
        """
        cmd = self.ssh_config.get_ssh_command_base()
        
        # Add reverse port forwarding
        # NOTE: 0.0.0.0 is used to allow connections to the remote port from any IP address
        cmd.extend([
            "-R", f"0.0.0.0:{self.remote_port}:{self.local_host}:{self.local_port}",
            "-N",  # Don't execute a remote command
            f"{self.ssh_config.jump_user}@{self.ssh_config.jump_server}"
        ])
        
        return self._establish_tunnel_common(cmd)
