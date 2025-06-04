#!/usr/bin/env python3
"""
Simple Observer Pattern Demo

一个简单的演示脚本，展示观察者模式进度条系统的基本功能。
不依赖复杂的网络连接，使用内存模拟的方式演示进度更新。
"""

import sys
import time
import threading
from pathlib import Path

# 添加项目根目录到Python路径
sys.path.insert(0, str(Path(__file__).parent.parent))

# 导入核心模块
try:
    from core.progress_observer import IProgressObserver, ProgressSubject
    from core.progress_events import (
        TaskStartedEvent, ProgressAdvancedEvent, TaskFinishedEvent, 
        TaskErrorEvent, generate_task_id
    )
    print("✓ 成功导入观察者模式核心模块")
except ImportError as e:
    print(f"✗ 导入核心模块失败: {e}")
    sys.exit(1)

# 尝试导入Rich相关模块
try:
    from rich.progress import Progress, TextColumn, BarColumn, TaskProgressColumn, TimeElapsedColumn
    from rich.console import Console
    RICH_AVAILABLE = True
    print("✓ Rich库可用")
except ImportError:
    RICH_AVAILABLE = False
    print("✗ Rich库不可用，将使用简单输出")

class SimpleProgressObserver(IProgressObserver):
    """简单的进度观察者实现（不依赖Rich）"""
    
    def __init__(self):
        self.tasks = {}
        self._lock = threading.Lock()
    
    def on_event(self, event):
        with self._lock:
            if isinstance(event, TaskStartedEvent):
                self.tasks[event.task_id] = {
                    'description': event.description,
                    'total': event.total,
                    'completed': 0,
                    'start_time': time.time()
                }
                print(f"[START] {event.description} (Total: {event.total:,} bytes)")
            
            elif isinstance(event, ProgressAdvancedEvent):
                if event.task_id in self.tasks:
                    task = self.tasks[event.task_id]
                    task['completed'] += event.advance
                    percent = (task['completed'] / task['total']) * 100
                    elapsed = time.time() - task['start_time']
                    rate = task['completed'] / elapsed if elapsed > 0 else 0
                    
                    print(f"[PROGRESS] {task['description']}: {percent:5.1f}% "
                          f"({task['completed']:>8,}/{task['total']:,} bytes) "
                          f"- {rate/1024/1024:.2f} MB/s")
            
            elif isinstance(event, TaskFinishedEvent):
                if event.task_id in self.tasks:
                    task = self.tasks[event.task_id]
                    elapsed = time.time() - task['start_time']
                    avg_rate = task['total'] / elapsed if elapsed > 0 else 0
                    
                    print(f"[FINISH] {event.description or task['description']} "
                          f"- 完成! 用时: {elapsed:.1f}s, 平均速度: {avg_rate/1024/1024:.2f} MB/s")
                    del self.tasks[event.task_id]
            
            elif isinstance(event, TaskErrorEvent):
                if event.task_id in self.tasks:
                    task = self.tasks[event.task_id]
                    print(f"[ERROR] {task['description']}: {event.error_message}")
                    del self.tasks[event.task_id]

class MockFileTransfer(ProgressSubject):
    """模拟文件传输类，用于演示观察者模式"""
    
    def __init__(self, name: str):
        super().__init__()
        self.name = name
    
    def simulate_transfer(self, file_name: str, file_size: int, chunk_size: int = 64*1024, delay: float = 0.01):
        """模拟文件传输过程"""
        task_id = generate_task_id()
        
        # 发布任务开始事件
        self.notify_observers(TaskStartedEvent(
            task_id=task_id,
            description=f"{self.name}: {file_name}",
            total=file_size
        ))
        
        try:
            transferred = 0
            while transferred < file_size:
                # 模拟传输延迟
                time.sleep(delay)
                
                # 计算本次传输的字节数
                remaining = file_size - transferred
                current_chunk = min(chunk_size, remaining)
                transferred += current_chunk
                
                # 发布进度更新事件
                self.notify_observers(ProgressAdvancedEvent(
                    task_id=task_id,
                    advance=current_chunk
                ))
                
                # 随机模拟一些传输速度变化
                if transferred > file_size * 0.3:
                    delay = max(0.005, delay * 0.95)  # 传输加速
            
            # 发布任务完成事件
            self.notify_observers(TaskFinishedEvent(
                task_id=task_id,
                description=f"{self.name}: {file_name} ✓ 完成",
                success=True
            ))
            
        except Exception as e:
            # 发布错误事件
            self.notify_observers(TaskErrorEvent(
                task_id=task_id,
                error_message=str(e)
            ))

def run_rich_demo():
    """使用Rich库的演示"""
    print("\n" + "="*60)
    print("Rich Progress Demo")
    print("="*60)
    
    console = Console()
    with Progress(
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=False,
        expand=True,
        auto_refresh=True
    ) as progress:
        
        # 创建Rich观察者（简化版，直接在这里实现）
        class RichObserver(IProgressObserver):
            def __init__(self, progress_instance):
                self.progress = progress_instance
                self.task_map = {}
                self._lock = threading.Lock()
            
            def on_event(self, event):
                with self._lock:
                    if isinstance(event, TaskStartedEvent):
                        rich_task_id = self.progress.add_task(
                            description=event.description,
                            total=event.total
                        )
                        self.task_map[event.task_id] = rich_task_id
                    
                    elif isinstance(event, ProgressAdvancedEvent):
                        rich_task_id = self.task_map.get(event.task_id)
                        if rich_task_id is not None:
                            self.progress.update(rich_task_id, advance=event.advance)
                    
                    elif isinstance(event, TaskFinishedEvent):
                        rich_task_id = self.task_map.get(event.task_id)
                        if rich_task_id is not None:
                            task = self.progress.tasks[rich_task_id]
                            self.progress.update(
                                rich_task_id,
                                completed=task.total,
                                description=event.description or task.description
                            )
                            del self.task_map[event.task_id]
                    
                    elif isinstance(event, TaskErrorEvent):
                        rich_task_id = self.task_map.get(event.task_id)
                        if rich_task_id is not None:
                            task = self.progress.tasks[rich_task_id]
                            self.progress.update(
                                rich_task_id,
                                description=task.description + " [red]✗ Error"
                            )
                            del self.task_map[event.task_id]
        
        rich_observer = RichObserver(progress)
        
        # 创建模拟传输实例
        sender1 = MockFileTransfer("发送端-1")
        sender2 = MockFileTransfer("发送端-2")
        receiver1 = MockFileTransfer("接收端-1")
        
        # 注册观察者
        for transfer in [sender1, sender2, receiver1]:
            transfer.add_observer(rich_observer)
        
        # 启动多个模拟传输任务
        threads = [
            threading.Thread(target=sender1.simulate_transfer, 
                           args=("large_file_1.dat", 10*1024*1024), name="Sender-1"),
            threading.Thread(target=sender2.simulate_transfer, 
                           args=("document.pdf", 5*1024*1024), name="Sender-2"),
            threading.Thread(target=receiver1.simulate_transfer, 
                           args=("backup.zip", 15*1024*1024), name="Receiver-1"),
        ]
        
        print("启动模拟传输任务...")
        for i, t in enumerate(threads):
            t.start()
            time.sleep(0.3)  # 错开启动时间
        
        # 等待所有任务完成
        for t in threads:
            t.join()
        
        print("\n所有任务完成!")

def run_simple_demo():
    """使用简单输出的演示"""
    print("\n" + "="*60)
    print("Simple Progress Demo")
    print("="*60)
    
    # 创建简单观察者
    observer = SimpleProgressObserver()
    
    # 创建模拟传输实例
    sender = MockFileTransfer("发送端")
    receiver = MockFileTransfer("接收端")
    
    # 注册观察者
    sender.add_observer(observer)
    receiver.add_observer(observer)
    
    # 启动模拟传输任务
    threads = [
        threading.Thread(target=sender.simulate_transfer, 
                       args=("test_file.dat", 3*1024*1024, 128*1024, 0.02), name="Sender"),
        threading.Thread(target=receiver.simulate_transfer, 
                       args=("received_file.dat", 2*1024*1024, 64*1024, 0.015), name="Receiver"),
    ]
    
    print("启动模拟传输任务...")
    for t in threads:
        t.start()
        time.sleep(0.5)
    
    # 等待所有任务完成
    for t in threads:
        t.join()
    
    print("\n所有任务完成!")

def main():
    """主函数"""
    print("Observer Pattern Progress System Demo")
    print("观察者模式进度条系统演示")
    
    # 基础功能测试
    print("\n1. 测试事件生成...")
    task_id = generate_task_id()
    start_event = TaskStartedEvent(task_id, "测试任务", 1024)
    progress_event = ProgressAdvancedEvent(task_id, 512)
    finish_event = TaskFinishedEvent(task_id, "测试任务完成", True)
    
    print(f"✓ 任务ID: {task_id}")
    print(f"✓ 开始事件: {start_event.description} (总大小: {start_event.total})")
    print(f"✓ 进度事件: 推进 {progress_event.advance} 字节")
    print(f"✓ 完成事件: {finish_event.description}")
    
    # 运行演示
    if RICH_AVAILABLE:
        run_rich_demo()
    else:
        run_simple_demo()
    
    print("\n" + "="*60)
    print("演示完成! 观察者模式进度条系统工作正常。")
    print("="*60)

if __name__ == "__main__":
    main()
