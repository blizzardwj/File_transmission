# File Transmission Application

This document provides an overview of the File Transmission application and its components. It features a sender and receiver that communicate over a jump server using SSH tunnels. The application is designed to transfer a file efficiently through sockets while providing real-time progress updates.

## Real performance
- The sender uses a broadband connection in China with an upload speed of 40 Mbps. 
- The receiver is located on the west coast of North America, with a network latency exceeding 230 ms.
- The following images demonstrate the actual transmission performance. The application logs the transfer rate after every 100 chunks sent. The average transfer rate is calculated by averaging these sampled rates. By default, the transfer rate is recorded once every 100 chunks, which provides a coarse measurement. For more accurate insights, refer to the real-time logging output, which better reflects the actual transfer speed.

**Example sender log output:**
![Sender Log Output](resources/sender_logger.png)

**Example receiver log output:**
![Receiver Log Output](resources/receiver_logger.png)

**Example sender log output upon completion:**
![Sender Completion Log Output](resources/sender_completion_logger.png)

## Start the application

For the sender:
```bash
python core/file_transfer_app.py --config config_sender.yml
```

For the receiver:
```bash
python core/file_transfer_app.py --config config_receiver.yml
```

## Class Diagram

The following diagram illustrates the main classes in `file_transfer_app.py` and their relationships with components from the `core` module:

```mermaid
classDiagram
    %% Entry Point
    class Main {
        <<entry>>
        +main(args: List~str~)
    }

    %% Abstract Base Application
    class FileTransferApp {
        <<abstract>>
        - config: Dict~str, Any~
        - ssh_config: SSHConfig
        - observer: IProgressObserver
        + _load_config(path: str): Dict
        + _create_ssh_config(): SSHConfig
        + _create_observer_if_enabled(): IProgressObserver
        + run_as_sender(): void
        + run_as_receiver(): void
    }
    class FileTransferSenderApp
    class FileTransferReceiverApp
    FileTransferSenderApp --|> FileTransferApp : <<instantiates>>
    FileTransferReceiverApp --|> FileTransferApp : <<instantiates>>

    %% SSH Configuration and Tunnels
    class SSHConfig {
        - host: str
        - port: int
        - username: str
        + load(path: str): SSHConfig
    }
    class SSHTunnel {
        <<interface>>
        + open(): void
        + close(): void
    }
    class SSHTunnelForward
    class SSHTunnelReverse
    SSHTunnelForward --|> SSHTunnel
    SSHTunnelReverse --|> SSHTunnel
    FileTransferApp *-- SSHConfig : "1"
    FileTransferApp *-- SSHTunnelForward : "0..1"
    FileTransferApp *-- SSHTunnelReverse : "0..1"

    %% Socket Transfer and Buffering
    class SocketTransferSubject {
        - buffer_manager: BufferManager
        - socket: socket
        + send(data: bytes): void
        + receive(): bytes
    }
    FileTransferApp o-- SocketTransferSubject : "1 manages"
    SocketTransferSubject o-- BufferManager : "1 uses"
    SocketTransferSubject *-- ProgressSubject : "publishes"

    class BufferManager {
        - buffer_size: int
        - max_buffer_size: int
        + adjust(latency: float): void
    }

    %% Observer Pattern
    class ProgressSubject {
        - observers: List~IProgressObserver~
        + attach(o: IProgressObserver): void
        + detach(o: IProgressObserver): void
        + notify(e: ProgressEvent): void
    }
    class IProgressObserver {
        <<interface>>
        + on_event(e: ProgressEvent): void
    }
    class RichProgressObserver {
        - console: Console
        + on_event(e: ProgressEvent): void
    }
    ProgressSubject o-- IProgressObserver : "*"
    RichProgressObserver --|> IProgressObserver
    IProgressObserver ..> ProgressEvent : "handles"

    class ProgressEvent {
        - event_type: str
        - bytes_transferred: int
        - timestamp: datetime
    }
    class TaskStartedEvent
    class ProgressAdvancedEvent
    class TaskCompletedEvent
    TaskStartedEvent --|> ProgressEvent
    ProgressAdvancedEvent --|> ProgressEvent
    TaskCompletedEvent --|> ProgressEvent

    %% Utilities
    class Utils {
        <<static>>
        + build_logger(name: str): Logger
        + get_shared_console(): Console
    }
    FileTransferApp ..> Utils : uses

    %% Entry Flow
    Main --> FileTransferSenderApp : "start"
    Main --> FileTransferReceiverApp : "start"
```
