#!/usr/bin/env python3
"""
Observer Pattern Progress Test

演示如何使用观察者模式的进度条系统来管理多线程文件传输的进度显示。
"""

import sys
import threading
import time
from pathlib import Path

# 添加项目根目录到Python路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.socket_data_transfer import SocketDataTransfer
from core.ssh_utils import BufferManager

# 尝试导入Rich进度条管理器
try:
    from core.rich_progress_observer import RichProgressObserver, create_progress_observer
    from rich.progress import Progress
    from rich.console import Console
    RICH_AVAILABLE = True
except ImportError as e:
    print(f"Warning: Rich not available ({e}), using fallback observer")
    RICH_AVAILABLE = False

def create_test_file(file_path: Path, size_mb: int = 10):
    """创建测试文件"""
    print(f"Creating test file: {file_path} ({size_mb} MB)")
    
    with open(file_path, 'wb') as f:
        # 写入指定大小的随机数据
        chunk_size = 1024 * 1024  # 1MB chunks
        for i in range(size_mb):
            # 创建一些可识别的数据模式
            data = f"Test data chunk {i:04d} ".encode('utf-8') * (chunk_size // 20)
            data = data[:chunk_size]  # 确保精确大小
            f.write(data)
    
    print(f"Test file created: {file_path.stat().st_size} bytes")

def sender_thread(sender: SocketDataTransfer, sock, file_path: Path, buffer_manager: BufferManager):
    """发送端线程"""
    try:
        print(f"[SENDER] Starting to send file: {file_path}")
        success = sender.send_file_adaptive(sock, file_path, buffer_manager, latency=0.01)
        if success:
            print(f"[SENDER] File sent successfully: {file_path.name}")
        else:
            print(f"[SENDER] Failed to send file: {file_path.name}")
    except Exception as e:
        print(f"[SENDER] Error: {e}")
    finally:
        sock.close()

def receiver_thread(receiver: SocketDataTransfer, sock, output_dir: Path, buffer_manager: BufferManager):
    """接收端线程"""
    try:
        print(f"[RECEIVER] Waiting to receive file in: {output_dir}")
        result_path = receiver.receive_file_adaptive(sock, output_dir, buffer_manager, latency=0.01)
        if result_path:
            print(f"[RECEIVER] File received successfully: {result_path}")
        else:
            print(f"[RECEIVER] Failed to receive file")
    except Exception as e:
        print(f"[RECEIVER] Error: {e}")
    finally:
        sock.close()

def run_observer_progress_test():
    """运行观察者模式进度条测试"""
    
    print("=" * 60)
    print("Observer Pattern Progress Bar Test")
    print("=" * 60)
    
    # 创建测试目录和文件
    test_dir = Path(__file__).parent / "test_data"
    test_dir.mkdir(exist_ok=True)
    
    output_dir = Path(__file__).parent / "test_received"
    output_dir.mkdir(exist_ok=True)
    
    # 创建测试文件
    test_file1 = test_dir / "test_file_1.dat"
    test_file2 = test_dir / "test_file_2.dat"
    
    if not test_file1.exists():
        create_test_file(test_file1, size_mb=5)
    if not test_file2.exists():
        create_test_file(test_file2, size_mb=8)
    
    # 创建缓冲管理器
    sender_buffer_manager = BufferManager(initial_size=64*1024, max_size=1024*1024)
    receiver_buffer_manager = BufferManager(initial_size=64*1024, max_size=1024*1024)
    
    # 创建SocketDataTransfer实例
    sender1 = SocketDataTransfer()
    sender2 = SocketDataTransfer()
    receiver1 = SocketDataTransfer()
    receiver2 = SocketDataTransfer()
    
    if RICH_AVAILABLE:
        print("\n使用Rich进度条显示...")
        
        # 创建Rich Progress实例并用作上下文管理器
        console = Console()
        with Progress(
            "[bold blue]{task.description}",
            "[progress.percentage]{task.percentage:>3.0f}%",
            "•",
            "[progress.completed]{task.completed:>7.0f}",
            "/",
            "[progress.total]{task.total:>7.0f}",
            "bytes",
            "•",
            "[progress.elapsed]{task.elapsed}",
            console=console,
            transient=False,
            expand=True,
            auto_refresh=True
        ) as progress:
            
            # 创建Rich观察者并设置给所有传输实例
            rich_observer = RichProgressObserver(progress_instance=progress, console=console)
            
            # 为所有传输实例注册观察者
            sender1.add_observer(rich_observer)
            sender2.add_observer(rich_observer)
            receiver1.add_observer(rich_observer)
            receiver2.add_observer(rich_observer)
            
            # 创建socket对连接
            import socket
            
            try:
                # 创建socket对用于传输
                sock1_send, sock1_recv = socket.socketpair()
                sock2_send, sock2_recv = socket.socketpair()
                
                # 启动传输线程
                print("\n启动文件传输任务...")
                
                threads = []
                
                # 任务1: 发送文件1
                t1 = threading.Thread(
                    target=sender_thread,
                    args=(sender1, sock1_send, test_file1, sender_buffer_manager),
                    name="Sender-1"
                )
                threads.append(t1)
                
                # 任务2: 接收文件1
                t2 = threading.Thread(
                    target=receiver_thread,
                    args=(receiver1, sock1_recv, output_dir, receiver_buffer_manager),
                    name="Receiver-1"
                )
                threads.append(t2)
                
                # 任务3: 发送文件2 (稍后启动)
                t3 = threading.Thread(
                    target=sender_thread,
                    args=(sender2, sock2_send, test_file2, sender_buffer_manager),
                    name="Sender-2"
                )
                threads.append(t3)
                
                # 任务4: 接收文件2
                t4 = threading.Thread(
                    target=receiver_thread,
                    args=(receiver2, sock2_recv, output_dir, receiver_buffer_manager),
                    name="Receiver-2"
                )
                threads.append(t4)
                
                # 启动所有线程
                for t in threads:
                    t.start()
                    time.sleep(0.5)  # 错开启动时间以便观察
                
                # 等待所有线程完成
                for t in threads:
                    t.join()
                
                print("\n所有传输任务完成!")
                
                # 移除观察者（清理）
                sender1.remove_observer(rich_observer)
                sender2.remove_observer(rich_observer)
                receiver1.remove_observer(rich_observer)
                receiver2.remove_observer(rich_observer)
                
            except Exception as e:
                print(f"传输过程中发生错误: {e}")
                import traceback
                traceback.print_exc()
    
    else:
        print("\n使用后备进度显示...")
        
        # 创建后备观察者
        fallback_observer = create_progress_observer(use_rich=False)
        
        # 为所有传输实例注册观察者
        sender1.add_observer(fallback_observer)
        sender2.add_observer(fallback_observer)
        receiver1.add_observer(fallback_observer)
        receiver2.add_observer(fallback_observer)
        
        # 模拟简单的传输
        print("由于Rich不可用，这里只显示基本的进度信息")
        print("实际的传输逻辑与Rich版本相同...")
    
    print("\n测试完成!")

if __name__ == "__main__":
    run_observer_progress_test()
