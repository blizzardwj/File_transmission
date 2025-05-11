#!/usr/bin/env python3
"""
File Transfer Application

This script provides a convenient interface for file transfers using YAML configuration.
It reads configuration from config.yml and initiates file transfer in sender or receiver mode.
"""

import os
import sys
import yaml
import argparse
import logging
from typing import Dict, Any

from core.file_transfer import FileSender, FileReceiver
from core.ssh_utils import SSHConfig
from core.utils import ConfigLoader, build_logger

# Setup logging
logger = build_logger(__name__)


class TransferApplication:
    """
    Main application class that orchestrates the file transfer
    """
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.ssh_config = self._create_ssh_config()
        self.transfer_port = self.config.get('transfer', {}).get('port', 9898)
        
    def _create_ssh_config(self) -> SSHConfig:
        """
        Create an SSHConfig object from the configuration
        
        Returns:
            SSHConfig object
        """
        ssh_config = SSHConfig(
            hostname=self.config['ssh']['jump_server'],
            username=self.config['ssh']['jump_user'],
            port=self.config['ssh'].get('jump_port', 22),
            identity_file=self.config['ssh'].get('identity_file')
        )
        
        return ssh_config
        
    def run(self):
        """
        Run the file transfer in the configured mode
        """
        # Sender mode
        if self.config.get('sender', {}).get('enabled'):
            file_path = self.config['sender']['file']
            logger.info(f"Starting in sender mode, file: {file_path}")
            
            sender = FileSender(self.ssh_config, self.transfer_port)
            success = sender.send_file(file_path)
            
            if success:
                logger.info("File transfer completed successfully")
            else:
                logger.error("File transfer failed")
                
        # Receiver mode
        elif self.config.get('receiver', {}).get('enabled'):
            output_dir = self.config['receiver'].get('output_dir', '.')
            logger.info(f"Starting in receiver mode, output directory: {output_dir}")
            
            receiver = FileReceiver(self.ssh_config, self.transfer_port)
            success = receiver.start_receiver(output_dir)
            
            if success:
                logger.info("Receiver stopped")
            else:
                logger.error("Receiver encountered an error")


def main():
    """
    Main entry point for the script
    """
    parser = argparse.ArgumentParser(description="File transfer using YAML configuration")
    parser.add_argument('--config', default='config.yml', help='Path to config file')
    args = parser.parse_args()
    
    # Load and validate configuration
    config_loader = ConfigLoader(args.config)
    config = config_loader.load_config()
    
    if not config_loader.validate_config():
        logger.error("Invalid configuration, aborting")
        sys.exit(1)
    
    # Run the application
    app = TransferApplication(config)
    try:
        app.run()
    except KeyboardInterrupt:
        logger.info("Application stopped by user")
    except Exception as e:
        logger.error(f"Error running application: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
