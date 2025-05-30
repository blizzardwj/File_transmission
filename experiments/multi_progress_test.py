#!/usr/bin/env python3
"""
多进度条显示测试

实验目标：使用 tqdm 和 rich 库实现多进度条显示
1. 在不同线程中分别显示进度条，而不会相互覆盖。
2. 在任务结束的时候，进度条不会消失，保留在终端中。
3. 模拟的文件传输过程会显示发送和接收的进度。

现有问题：
```
===== tqdm 多进度条测试 use position =====


发送 大型文档.pdf: 100%|####################################################| 105M/105M [00:07<00:00, 14.4MB/s]
接收 重要数据.zip: 100%|##################################################| 83.9M/83.9M [00:09<00:00, 8.81MB/s]
接收 重要数据.zip:  94%|##############################################8   | 78.6M/83.9M [00:07<00:01, 3.91MB/s]
tqdm 测试完成!
```

接收进度条在任务结束前后显示两次，原因在于 tqdm obj 在 __exit__ 中会调用 close()，使用了如下代码：
```
        with self._lock:
            if leave:
                # stats for overall rate (no weighted average)
                self._ema_dt = lambda: None
                self.display(pos=0)
                fp_write('\n')
            else:
                # clear previous display
                if self.display(msg='', pos=pos) and not pos:
                    fp_write('\r')
```
fp_write('\n') 会将光标移动到下一行，如果两个线程中的send_file progress bar 和 receive_file progress bar 分别在 position=0 和 position=1， 那么

1. fp_write('\n') 会将光标移动到下一行之后, position=1 的位置是否还是相对于当前光标的行数？
Answer: 是的， position=1 的位置还是相对于当前光标的行数。

2. 如果 send_file obj 在 __exit__ 中调用 close() 后， receive_file obj 调用了 update()，是否会在原来行的下一行显示，导致原始的行没有更新？
Answer： 是的。

3. 逻辑上当fp_write('\n') 之后，所有 使用update 的 obj，position都要 减少1。这样是不是更合理？
Answer： 虽然从逻辑上合理，但是实现复杂度较高。
    首先修改涉及全局状态管理，需要维护所有活跃的tqdm对象注册表，
    其次涉及通信机制，需要线程安全的通信，
    最后要保证操作原子性，位置调整必须是原子操作。




依赖:
- tqdm
- rich
"""

import os
import sys
import time
import threading
import random
from pathlib import Path
from typing import Iterator, Optional, Any

# 检查是否安装了所需的库，如果没有则提示安装
try:
    from tqdm import tqdm
    TQDM_AVAILABLE = True
    tqdm_module = tqdm
except ImportError:
    TQDM_AVAILABLE = False
    tqdm_module = None
    print("tqdm 库未安装，请使用命令 'pip install tqdm' 安装")

try:
    from rich.progress import Progress, TextColumn, BarColumn, TaskProgressColumn, TimeRemainingColumn, TimeElapsedColumn
    from rich.console import Console
    RICH_AVAILABLE = True
    rich_modules = {
        'Progress': Progress,
        'TextColumn': TextColumn,
        'BarColumn': BarColumn,
        'TaskProgressColumn': TaskProgressColumn,
        'TimeRemainingColumn': TimeRemainingColumn,
        'TimeElapsedColumn': TimeElapsedColumn,
        'Console': Console
    }
except ImportError:
    RICH_AVAILABLE = False
    rich_modules = {}
    print("rich 库未安装，请使用命令 'pip install rich' 安装")

# 模拟文件大小 (字节)
FILE_SIZE = 100 * 1024 * 1024  # 100 MB

def simulate_file_operation(file_size: int, chunk_size: int = 1024*1024, op_delay: float = 0.01, jitter: float = 0.005) -> Iterator[int]:
    """
    模拟文件操作（发送或接收），按块进行处理并返回每个块的大小
    
    Args:
        file_size: 文件总大小 (字节)
        chunk_size: 每次操作的数据块大小 (字节)
        op_delay: 每个操作的基础延迟 (秒)
        jitter: 随机延迟的最大值 (秒)
        
    Yields:
        每次迭代处理的数据块大小
    """
    processed = 0
    
    while processed < file_size:
        # 计算本次迭代要处理的数据大小
        remaining = file_size - processed
        current_chunk = min(chunk_size, remaining)
        
        # 模拟网络延迟和处理时间
        delay = op_delay + random.uniform(0, jitter)
        time.sleep(delay)
        
        # 增加已处理数据量
        processed += current_chunk
        
        # 返回本次处理的数据量
        yield current_chunk

# ========== tqdm 实现 ==========

class TqdmProgressManager:
    """tqdm 进度条管理器，确保线程安全和正确显示"""
    
    def __init__(self):
        self.lock = threading.Lock()
        self.progress_bars = {}
        self.coordination = {
            'send_finished': threading.Event(),
            'receive_finished': threading.Event()
        }
        
    def send_file(self, file_name: str, file_size: int) -> None:
        """
        使用 tqdm 模拟文件发送，显示进度条
        
        Args:
            file_name: 文件名称
            file_size: 文件大小 (字节)
        """
        if not TQDM_AVAILABLE or tqdm_module is None:
            print("无法使用 tqdm 发送文件: 库未安装")
            return
            
        # 设置 tqdm 的线程锁
        tqdm_module.set_lock(self.lock)
            
        # 使用上下文管理器创建 tqdm 进度条，自动关闭
        pbar_key = f"send_{threading.current_thread().ident}"
        with tqdm_module(
            total=file_size,
            unit='B',
            unit_scale=True,
            desc=f"发送 {file_name}",
            leave=True,
            position=0,
            dynamic_ncols=True,  # 动态调整宽度
            miniters=1,          # 最小更新间隔
            mininterval=0.1,     # 最小时间间隔
            ascii=True,          # 使用ASCII字符，提高兼容性
            ncols=100            # 固定列宽，避免显示问题
        ) as pbar:
            self.progress_bars[pbar_key] = pbar
            try:
                for chunk_size in simulate_file_operation(file_size):
                    pbar.update(chunk_size)
            finally:
                # 确保进度条完成
                self.coordination['send_finished'].set()
                # 清理进度条引用
                if pbar_key in self.progress_bars:
                    del self.progress_bars[pbar_key]

    def receive_file(self, file_name: str, file_size: int) -> None:
        """
        使用 tqdm 模拟文件接收，显示进度条
        
        Args:
            file_name: 文件名称
            file_size: 文件大小 (字节)
        """
        if not TQDM_AVAILABLE or tqdm_module is None:
            print("无法使用 tqdm 接收文件: 库未安装")
            return
            
        # 设置 tqdm 的线程锁
        tqdm_module.set_lock(self.lock)
            
        # 使用上下文管理器创建 tqdm 进度条，自动关闭
        pbar_key = f"receive_{threading.current_thread().ident}"
        with tqdm_module(
            total=file_size,
            unit='B',
            unit_scale=True,
            desc=f"接收 {file_name}",
            leave=True,
            position=1,
            dynamic_ncols=True,  # 动态调整宽度
            miniters=1,          # 最小更新间隔
            mininterval=0.1,     # 最小时间间隔
            ascii=True,          # 使用ASCII字符，提高兼容性
            ncols=100            # 固定列宽，避免显示问题
        ) as pbar:
            self.progress_bars[pbar_key] = pbar
            try:
                for chunk_size in simulate_file_operation(file_size, op_delay=0.015):
                    pbar.update(chunk_size)
            finally:
                # 确保进度条完成
                self.coordination['receive_finished'].set()
                # 清理进度条引用
                if pbar_key in self.progress_bars:
                    del self.progress_bars[pbar_key]

    def run_test(self) -> None:
        """运行 tqdm 多进度条测试"""
        if not TQDM_AVAILABLE:
            print("tqdm 测试已跳过: 库未安装")
            return
            
        print("\n===== tqdm 多进度条测试 use position =====")
        
        # 清理之前的状态
        self.coordination['send_finished'].clear()
        self.coordination['receive_finished'].clear()
        self.progress_bars.clear()
        
        # 设置全局锁，确保线程安全
        if TQDM_AVAILABLE and tqdm_module:
            tqdm_module.set_lock(self.lock)
        
        # 预留终端空间，防止进度条重叠
        print()  # 空行作为缓冲
        print()  # 空行作为缓冲
        
        # 创建线程用于发送和接收文件
        send_thread = threading.Thread(
            target=self.send_file,
            args=("大型文档.pdf", FILE_SIZE),
            daemon=False  # 确保主程序等待线程完成
        )
        
        receive_thread = threading.Thread(
            target=self.receive_file,
            args=("重要数据.zip", int(FILE_SIZE * 0.8)),  # 接收的文件稍小
            daemon=False  # 确保主程序等待线程完成
        )
        
        # 启动线程
        send_thread.start()
        # 稍微延迟启动第二个线程，确保第一个进度条已经初始化
        time.sleep(0.1)
        receive_thread.start()
        
        # 等待线程完成
        send_thread.join()
        receive_thread.join()
        
        # 等待事件确认所有进度条都已完成
        self.coordination['send_finished'].wait(timeout=5)
        self.coordination['receive_finished'].wait(timeout=5)
        
        # 等待一小段时间，确保所有输出完成
        time.sleep(0.3)
        
        print("\ntqdm 测试完成!\n")

# ========== rich 实现 ==========

class RichProgressManager:
    """Rich 进度条管理器"""
    
    def run_test(self) -> None:
        """运行 rich 多进度条测试"""
        if not RICH_AVAILABLE:
            print("rich 测试已跳过: 库未安装")
            return
            
        print("\n===== rich 多进度条测试 =====")
        
        # 创建 Rich Console 和 Progress 对象
        console = rich_modules['Console']()
        
        # 使用上下文管理器来确保进度条在完成后不会被清除
        with rich_modules['Progress'](
            rich_modules['TextColumn']("[bold blue]{task.description}"),
            rich_modules['BarColumn'](),
            rich_modules['TaskProgressColumn'](),
            rich_modules['TimeElapsedColumn'](),
            rich_modules['TimeRemainingColumn'](),
            console=console,
            transient=False,  # 保持进度条可见
            expand=True       # 扩展到整个控制台宽度
        ) as progress:
            # 添加发送和接收任务
            send_task = progress.add_task("[green]发送文件", total=FILE_SIZE)
            receive_task = progress.add_task("[yellow]接收文件", total=int(FILE_SIZE * 0.8))
            
            # 在单独的线程中运行发送任务
            def send_file_task():
                for chunk_size in simulate_file_operation(FILE_SIZE):
                    progress.update(send_task, advance=chunk_size)
                # 完成后更新描述
                progress.update(send_task, description="[green]发送完成")
            
            # 在单独的线程中运行接收任务
            def receive_file_task():
                for chunk_size in simulate_file_operation(int(FILE_SIZE * 0.8), op_delay=0.015):
                    progress.update(receive_task, advance=chunk_size)
                # 完成后更新描述
                progress.update(receive_task, description="[yellow]接收完成")
            
            # 创建并启动线程
            send_thread = threading.Thread(target=send_file_task)
            receive_thread = threading.Thread(target=receive_file_task)
            
            send_thread.start()
            receive_thread.start()
            
            # 等待线程完成
            send_thread.join()
            receive_thread.join()
        
        print("\nrich 测试完成!\n")

def main():
    """主函数：运行所有测试"""
    print("开始多进度条显示测试...\n")
    
    # 检查是否安装了任一库
    if not TQDM_AVAILABLE and not RICH_AVAILABLE:
        print("错误: 请至少安装 tqdm 或 rich 库")
        print("安装命令: pip install tqdm rich")
        return
    
    # 运行 tqdm 测试
    tqdm_manager = TqdmProgressManager()
    tqdm_manager.run_test()
    
    # 运行 rich 测试
    rich_manager = RichProgressManager()
    rich_manager.run_test()
    
    print("所有测试已完成！")

if __name__ == "__main__":
    main()
