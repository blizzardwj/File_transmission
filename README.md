# File Transmission Application

This document provides an overview of the File Transmission application and its components.

## Start the application

For the sender:
```bash
python transfer.py --config config_sender.yml
```

For the receiver:
```bash
python transfer.py --config config_receiver.yml
```

## Class Diagram

The following diagram illustrates the main classes in `transfer.py` and their relationships with components from the `core` module:

```mermaid
classDiagram
    class TransferApplication {
        +config: Dict
        +ssh_config: SSHConfig
        +transfer_port: int
        +_create_ssh_config() SSHConfig
        +run()
    }
    class SSHConfig {
        +hostname: str
        +username: str
        +port: int
        +identity_file: str
        +use_password: bool
    }
    class FileSender {
        # Inherits from FileTransferBase
        # Uses SSHTunnelForward
        # Uses SocketDataTransfer
    }
    class FileReceiver {
        # Inherits from FileTransferBase
        # Uses SSHTunnelReverse
        # Uses SocketDataTransfer
    }
    class ConfigLoader {
        +config_file: str
        +load_config() Dict
        +validate_config() bool
    }
    class main_function {
        <<script>>
        # Entry point for transfer.py
    }

    class FileTransferBase {
        +mode: TransferMode
        +ssh_config: SSHConfig
        +buffer_manager: BufferManager
        +network_monitor: NetworkMonitor
        +tunnel: Object
    }
    class BufferManager {
        # Manages buffer sizes
    }
    class NetworkMonitor {
        # Monitors network conditions
    }
    class SSHTunnelForward {
        # Establishes forward SSH tunnel
    }
    class SSHTunnelReverse {
        # Establishes reverse SSH tunnel
    }
    class SocketDataTransfer {
        # Handles raw socket data transfer
    }

    TransferApplication "1" *-- "1" SSHConfig : uses
    TransferApplication ..> FileSender : uses
    TransferApplication ..> FileReceiver : uses

    main_function ..> ConfigLoader : uses
    main_function ..> TransferApplication : creates & runs

    FileSender --|> FileTransferBase
    FileReceiver --|> FileTransferBase

    FileTransferBase "1" *-- "1" BufferManager : uses
    FileTransferBase "1" *-- "1" NetworkMonitor : uses

    FileSender ..> SSHTunnelForward : uses
    FileReceiver ..> SSHTunnelReverse : uses

    FileSender ..> SocketDataTransfer : uses
    FileReceiver ..> SocketDataTransfer : uses
```
