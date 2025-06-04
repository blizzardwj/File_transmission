# Progress Observer Pattern Usage Guide

本文档说明如何使用基于观察者模式的进度条系统来管理多线程文件传输的进度显示。

## 概述

观察者模式进度条系统由以下组件组成：

1. **`ProgressEvent`** 类族：定义各种进度事件类型
2. **`IProgressObserver`** 接口：观察者必须实现的接口
3. **`ProgressSubject`** 类：事件发布者基类
4. **`RichProgressObserver`** 类：基于 Rich 库的进度条观察者实现
5. **`SocketDataTransfer`** 类：继承自 `ProgressSubject`，在文件传输时发布进度事件

## 系统架构

```
SocketDataTransfer (Subject/Publisher)
        |
        | notify_observers(event)
        |
        v
IProgressObserver (Interface)
        ^
        |
        | implements
        |
RichProgressObserver (Concrete Observer)
        |
        | updates
        |
        v
Rich Progress Display
```

## 使用方法

### 基本使用示例

```python
from core.socket_data_transfer import SocketDataTransfer
from core.rich_progress_manager import RichProgressObserver
from rich.progress import Progress
from rich.console import Console

# 创建 Rich Progress 实例
console = Console()
with Progress(console=console, transient=False, expand=True) as progress:
    
    # 创建进度观察者
    progress_observer = RichProgressObserver(progress_instance=progress)
    
    # 创建传输实例并注册观察者
    sender = SocketDataTransfer()
    receiver = SocketDataTransfer()
    
    sender.add_observer(progress_observer)
    receiver.add_observer(progress_observer)
    
    # 执行文件传输（这将自动发布进度事件）
    # sender.send_file_adaptive(sock, file_path, buffer_manager)
    # receiver.receive_file_adaptive(sock, output_dir, buffer_manager)
    
    # 清理（可选）
    sender.remove_observer(progress_observer)
    receiver.remove_observer(progress_observer)
```

### 多线程传输示例

```python
import threading
from core.socket_data_transfer import SocketDataTransfer
from core.rich_progress_manager import RichProgressObserver
from rich.progress import Progress

def sender_thread(sender, sock, file_path, buffer_manager):
    """发送端线程"""
    sender.send_file_adaptive(sock, file_path, buffer_manager)

def receiver_thread(receiver, sock, output_dir, buffer_manager):
    """接收端线程"""
    receiver.receive_file_adaptive(sock, output_dir, buffer_manager)

# 使用 Rich Progress 作为上下文管理器
with Progress(transient=False, expand=True) as progress:
    
    # 创建共享的进度观察者
    progress_observer = RichProgressObserver(progress_instance=progress)
    
    # 创建多个传输实例
    sender1 = SocketDataTransfer()
    sender2 = SocketDataTransfer()
    receiver1 = SocketDataTransfer()
    receiver2 = SocketDataTransfer()
    
    # 为所有实例注册相同的观察者
    for instance in [sender1, sender2, receiver1, receiver2]:
        instance.add_observer(progress_observer)
    
    # 启动多个传输线程
    threads = [
        threading.Thread(target=sender_thread, args=(sender1, sock1, file1, bm1)),
        threading.Thread(target=receiver_thread, args=(receiver1, sock2, dir1, bm2)),
        # ... 更多线程
    ]
    
    for t in threads:
        t.start()
    
    for t in threads:
        t.join()
```

### 自定义观察者实现

```python
from core.progress_observer import IProgressObserver
from core.progress_events import (
    TaskStartedEvent, ProgressAdvancedEvent, TaskFinishedEvent, TaskErrorEvent
)

class CustomProgressObserver(IProgressObserver):
    """自定义进度观察者示例"""
    
    def __init__(self):
        self.tasks = {}
    
    def on_event(self, event):
        if isinstance(event, TaskStartedEvent):
            self.tasks[event.task_id] = {
                'description': event.description,
                'total': event.total,
                'completed': 0
            }
            print(f"Started: {event.description} (Total: {event.total} bytes)")
        
        elif isinstance(event, ProgressAdvancedEvent):
            if event.task_id in self.tasks:
                self.tasks[event.task_id]['completed'] += event.advance
                task = self.tasks[event.task_id]
                percent = (task['completed'] / task['total']) * 100
                print(f"Progress: {task['description']} - {percent:.1f}%")
        
        elif isinstance(event, TaskFinishedEvent):
            if event.task_id in self.tasks:
                print(f"Completed: {event.description}")
                del self.tasks[event.task_id]
        
        elif isinstance(event, TaskErrorEvent):
            print(f"Error: {event.error_message}")
            if event.task_id in self.tasks:
                del self.tasks[event.task_id]

# 使用自定义观察者
custom_observer = CustomProgressObserver()
transfer = SocketDataTransfer()
transfer.add_observer(custom_observer)
```

## 事件类型

### TaskStartedEvent
- 任务开始时触发
- 包含：task_id, description, total (文件大小)

### ProgressAdvancedEvent  
- 进度推进时触发
- 包含：task_id, advance (本次进步的字节数)

### TaskFinishedEvent
- 任务成功完成时触发
- 包含：task_id, description (可选), success (布尔值)

### TaskErrorEvent
- 任务出错时触发
- 包含：task_id, error_message

## 配置选项

### RichProgressObserver 配置

```python
from rich.progress import Progress, TextColumn, BarColumn, TaskProgressColumn
from rich.console import Console

# 自定义 Progress 样式
console = Console()
progress = Progress(
    TextColumn("[bold blue]{task.description}"),
    BarColumn(),
    TaskProgressColumn(),
    TimeElapsedColumn(),
    TimeRemainingColumn(),
    console=console,
    transient=False,  # 保持已完成任务的显示
    expand=True,      # 展开进度条到终端宽度
    auto_refresh=True # 自动刷新
)

# 创建观察者
observer = RichProgressObserver(progress_instance=progress, console=console)
```

### 无 Rich 库的后备方案

```python
from core.rich_progress_manager import create_progress_observer

# 自动选择合适的观察者（Rich 可用时使用 Rich，否则使用简单打印）
observer = create_progress_observer(use_rich=True)

# 强制使用简单打印观察者
fallback_observer = create_progress_observer(use_rich=False)
```

## 最佳实践

1. **生命周期管理**：使用 `with` 语句管理 Rich Progress 实例的生命周期
2. **共享观察者**：多个传输实例可以共享同一个观察者实例
3. **错误处理**：观察者内部应捕获异常，避免影响其他观察者
4. **线程安全**：RichProgressObserver 内部使用锁确保线程安全
5. **资源清理**：传输完成后移除观察者（可选，但推荐）

## 注意事项

- Rich 库是可选依赖，系统会自动降级到简单的打印输出
- 观察者模式确保了 SocketDataTransfer 与进度显示的解耦
- 每个传输任务会生成唯一的 task_id 用于跟踪
- 进度事件在传输线程中异步发布，不会阻塞传输过程

## 故障排除

### 常见问题

1. **导入错误**：确保 Rich 库已安装 (`pip install rich`)
2. **进度条不显示**：检查是否正确注册了观察者
3. **进度条重叠**：确保使用同一个 Progress 实例的观察者
4. **内存泄漏**：大量传输后确保移除观察者引用

### 调试技巧

```python
# 检查观察者是否正确注册
print(f"Observer count: {transfer.get_observer_count()}")

# 检查活跃任务数量
print(f"Active tasks: {rich_observer.get_active_task_count()}")

# 启用详细日志
import logging
logging.getLogger('core.socket_data_transfer').setLevel(logging.DEBUG)
