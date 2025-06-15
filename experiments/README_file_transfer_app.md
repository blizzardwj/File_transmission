# File Transfer Application - Refactored Version

This document describes the refactored file transfer application with reverse SSH tunnel support.

## Overview

The application has been refactored from `experiments/reverse_ssh_tunnel.py` to be more modular and configuration-driven. It now supports:

- **Configuration-driven operation**: All settings are loaded from YAML files
- **Unified entry point**: Single application that acts as sender or receiver based on configuration
- **Modular design**: Clean separation of concerns with reusable components
- **Progress observer integration**: Built-in support for Rich progress bars

## Usage

### Basic Command Structure

```bash
# Run as sender
python experiments/file_transfer_app.py --config config_sender.yml

# Run as receiver
python experiments/file_transfer_app.py --config config_receiver.yml
```

### Configuration Files

The application uses two configuration files:

#### config_sender.yml
- Sets `sender.enabled: true` and `receiver.enabled: false`
- Specifies the file to send in `sender.file`
- Configures SSH connection and tunnel settings

#### config_receiver.yml
- Sets `receiver.enabled: true` and `sender.enabled: false`
- Specifies output directory for received files in `receiver.output_dir`
- Configures SSH connection and tunnel settings

### Key Improvements

1. **No more hardcoded DEBUG_CONFIG**: All configuration is externalized to YAML files
2. **Role-based operation**: Clear separation between sender and receiver roles
3. **Simplified naming**: Functions renamed to reflect actual purpose (no more "simulate")
4. **Enhanced modularity**: Uses `ObserverContext` from `core.file_transfer_app` 
5. **Better error handling**: Comprehensive configuration validation

### Configuration Structure

```yaml
# SSH Connection
ssh:
  jump_server: "192.168.31.123"
  jump_user: "root"
  jump_port: 22
  use_password: true
  password: null  # Will prompt if not provided

# Tunnel Configuration
transfer:
  remote_port: 8022
  local_port: 9022

# Role Configuration (enable only one)
sender:
  enabled: true/false
  file: "path/to/file/to/send"

receiver:
  enabled: true/false
  output_dir: "~/received_files"

# Performance and Progress
performance:
  use_adaptive_transfer: true

progress:
  use_progress_observer: true
  use_rich_progress: true

# Operation Mode
mode: "file"  # or "message"
```

### Example Workflow

1. **Start Receiver** (on one machine):
   ```bash
   python experiments/file_transfer_app.py --config config_receiver.yml
   ```

2. **Start Sender** (on another machine or after receiver is ready):
   ```bash
   python experiments/file_transfer_app.py --config config_sender.yml
   ```

3. The sender will establish the reverse tunnel and send the configured file to the receiver.

### Features Retained

- All original functionality from `reverse_ssh_tunnel.py`
- Progress observer integration with Rich progress bars
- Adaptive buffering for optimal transfer performance
- Comprehensive error handling and logging
- SSH tunnel management and cleanup

### Migration from Original Script

To migrate from the original `reverse_ssh_tunnel.py`:

1. Create configuration files based on your `DEBUG_CONFIG` settings
2. Replace script calls with the new `--config` parameter approach
3. Update any automation scripts to use the new command structure

The core functionality remains identical, but the interface is now cleaner and more maintainable.
