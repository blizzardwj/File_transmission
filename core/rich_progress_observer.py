#!/usr/bin/env python3
"""
Rich Progress Manager Module

基于 Rich 库实现的进度条观察者，用于在终端中显示多线程文件传输进度。
"""

from typing import Dict, Optional
import threading
from core.progress_observer import IProgressObserver
from core.progress_events import (
    ProgressEvent, TaskStartedEvent, ProgressAdvancedEvent, 
    TaskFinishedEvent, TaskErrorEvent
)
from core.utils import build_logger

logger = build_logger(__name__)

# 尝试导入 Rich 库
try:
    from rich.progress import (
        Progress, TextColumn, BarColumn, TaskProgressColumn, 
        TimeRemainingColumn, TimeElapsedColumn, TaskID
    )
    from rich.console import Console
    from rich.table import Column
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False
    logger.warning("Rich library not available. Progress display will be disabled.")

# 全局共享的 RichProgressObserver 实例和锁
_shared_rich_observer_instance: Optional['RichProgressObserver'] = None
_shared_console_instance: Optional['Console'] = None
_shared_observer_lock = threading.Lock()

class RichProgressObserver(IProgressObserver):
    """
    基于 Rich 库的进度条观察者
    
    监听进度事件并更新 Rich 进度条显示
    """
    
    def __init__(self, 
        progress_instance: Optional['Progress'] = None, 
        console: Optional['Console'] = None,
        manage_lifecycle: bool = True
    ):
        """
        初始化 Rich 进度条观察者
        
        Args:
            progress_instance: 外部传入的 Rich Progress 实例，如果为 None 则自动创建
            console: Rich Console 实例，如果为 None 则使用默认控制台
            manage_lifecycle: 是否管理 Progress 实例的生命周期（start/stop）
        """
        if not RICH_AVAILABLE:
            raise ImportError("Rich library is required for RichProgressObserver")
        
        self._console = console or Console()
        self._rich_task_map: Dict[str, TaskID] = {}  # 映射任务ID到Rich TaskID
        self._lock = threading.Lock()  # 保护任务映射的线程安全
        self._external_progress = progress_instance is not None
        self._manage_lifecycle = manage_lifecycle  # 新增：是否管理生命周期
        
        if self._external_progress:
            self._progress_instance = progress_instance
        else:
            # 如果没有外部 Progress 实例，创建内部实例
            self._progress_instance = Progress(
                TextColumn(
                    "[bold blue]{task.description}", 
                    table_column=Column(overflow="fold")
                ),
                BarColumn(),
                TaskProgressColumn(),
                TimeElapsedColumn(),
                TimeRemainingColumn(),
                console=self._console,
                transient=False,  # 保持进度条显示，不在完成后清除
                expand=True,
                auto_refresh=True
            )
    
    @property
    def progress(self) -> 'Progress':
        """获取 Progress 实例"""
        return self._progress_instance
    
    @property
    def has_living_observers(self) -> bool:
        """
        检查是否有活跃的观察者
        
        Returns:
            bool: 如果有活跃的任务，则返回 True，否则返回 False
        """
        with self._lock:
            return len(self._rich_task_map) > 0
        
    def start(self) -> None:
        """启动进度条显示（仅在内部管理 Progress 实例时有效）"""
        if not self._external_progress and self._manage_lifecycle and self._progress_instance:
            # 使用 Rich Progress 的 live.is_started 属性来检查启动状态
            if self._progress_instance.live and not self._progress_instance.live.is_started:
                try:
                    self._progress_instance.start()
                    logger.debug("Rich progress started by RichProgressObserver")
                except Exception as e:
                    logger.error(f"Failed to start Rich progress: {e}")
            elif not self._progress_instance.live:
                logger.warning("Progress instance has no 'live' object to check 'is_started'.")
    
    def stop(self) -> None:
        """停止进度条显示（仅在内部管理 Progress 实例时有效）"""
        if not self._external_progress and self._manage_lifecycle and self._progress_instance:
            # 只有在没有活跃任务时才停止
            with self._lock:
                if not self._rich_task_map:
                    # 使用 Rich Progress 的 live.is_started 属性来检查启动状态
                    if self._progress_instance.live and self._progress_instance.live.is_started:
                        try:
                            self._progress_instance.stop()
                            logger.debug("Rich progress stopped by RichProgressObserver")
                        except Exception as e:
                            logger.error(f"Failed to stop Rich progress: {e}")
                    elif not self._progress_instance.live:
                        logger.warning("Progress instance has no 'live' object to check 'is_started' for stopping.")
    
    def __enter__(self):
        """上下文管理器入口"""
        self.start()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器出口"""
        self.stop()
    
    def on_event(self, event: ProgressEvent) -> None:
        """
        处理进度事件
        
        Args:
            event: 进度事件对象
        """
        if not RICH_AVAILABLE or not self._progress_instance:
            return
        
        try:
            if isinstance(event, TaskStartedEvent):
                self._handle_task_started(event)
            elif isinstance(event, ProgressAdvancedEvent):
                self._handle_progress_advanced(event)
            elif isinstance(event, TaskFinishedEvent):
                self._handle_task_finished(event)
            elif isinstance(event, TaskErrorEvent):
                self._handle_task_error(event)
            else:
                logger.debug(f"Unhandled event type: {type(event).__name__}")
        except Exception as e:
            logger.error(f"Error handling progress event: {e}")
    
    def _handle_task_started(self, event: TaskStartedEvent) -> None:
        """处理任务开始事件"""
        with self._lock:
            if event.task_id not in self._rich_task_map:
                rich_task_id = self._progress_instance.add_task(
                    description=event.description,
                    total=event.total
                )
                self._rich_task_map[event.task_id] = rich_task_id
                logger.debug(f"Started task '{event.description}' with ID {event.task_id}")
    
    def _handle_progress_advanced(self, event: ProgressAdvancedEvent) -> None:
        """处理进度推进事件"""
        with self._lock:
            rich_task_id = self._rich_task_map.get(event.task_id)
            if rich_task_id is not None:
                update_kwargs = {"advance": event.advance}
                if event.description:
                    update_kwargs["description"] = event.description
                
                self._progress_instance.update(rich_task_id, **update_kwargs)
            else:
                logger.warning(f"Progress advance for unknown task ID: {event.task_id}")
    
    def _handle_task_finished(self, event: TaskFinishedEvent) -> None:
        """处理任务完成事件"""
        with self._lock:
            rich_task_id = self._rich_task_map.get(event.task_id)
            if rich_task_id is not None:
                # 获取当前任务状态
                task = self._progress_instance.tasks[rich_task_id]
                
                # 确保任务完成到100%
                self._progress_instance.update(
                    rich_task_id, 
                    completed=task.total,
                    description=event.description or (task.description + " [green]✓ Done")
                )

                # 从映射中移除
                del self._rich_task_map[event.task_id]
                logger.debug(f"Finished task with ID {event.task_id}")
            else:
                logger.warning(f"Task finish for unknown task ID: {event.task_id}")
    
    def _handle_task_error(self, event: TaskErrorEvent) -> None:
        """处理任务错误事件"""
        with self._lock:
            rich_task_id = self._rich_task_map.get(event.task_id)
            if rich_task_id is not None:
                # 标记任务为错误状态
                task = self._progress_instance.tasks[rich_task_id]
                self._progress_instance.update(
                    rich_task_id,
                    description=task.description + " [red]✗ Error"
                )
                
                # 从映射中移除
                del self._rich_task_map[event.task_id]
                logger.error(f"Task error for ID {event.task_id}: {event.error_message}")
            else:
                logger.warning(f"Task error for unknown task ID: {event.task_id}")
    
    def get_active_task_count(self) -> int:
        """获取当前活跃任务数量"""
        with self._lock:
            return len(self._rich_task_map)

class SimpleFallbackObserver(IProgressObserver):
    """
    简单的后备观察者实现，在 Rich 不可用时使用
    
    使用标准的 print 输出显示进度信息
    """
    
    def __init__(self):
        self._lock = threading.Lock()
    
    def __enter__(self):
        """上下文管理器入口"""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器出口"""
        return False
    
    def on_event(self, event: ProgressEvent) -> None:
        """处理进度事件（简单打印方式）"""
        with self._lock:
            if isinstance(event, TaskStartedEvent):
                print(f"[STARTED] {event.description} (Total: {event.total})")
            elif isinstance(event, ProgressAdvancedEvent):
                print(f"[PROGRESS] Task {event.task_id}: +{event.advance}")
            elif isinstance(event, TaskFinishedEvent):
                status = "SUCCESS" if event.success else "FAILED"
                desc = event.description or f"Task {event.task_id}"
                print(f"[{status}] {desc}")
            elif isinstance(event, TaskErrorEvent):
                print(f"[ERROR] Task {event.task_id}: {event.error_message}")

def get_shared_rich_observer(console: Optional['Console'] = None) -> Optional[RichProgressObserver]:
    """
    返回共享的 RichProgressObserver 实例
    第一次调用时创建并启动底层的 Progress 对象
    
    Args:
        console: Rich Console 实例
        
    Returns:
        共享的 RichProgressObserver 实例，如果 Rich 不可用则返回 None
    """
    global _shared_rich_observer_instance
    if not RICH_AVAILABLE:
        return None

    with _shared_observer_lock:
        if _shared_rich_observer_instance is None:
            try:
                # 第一个实例将管理其内部 Progress 对象的生命周期
                _shared_rich_observer_instance = RichProgressObserver(
                    console=console, 
                    manage_lifecycle=True
                )
                # 立即启动，因为它是应用程序会话的长期实例
                _shared_rich_observer_instance.start()
                logger.info("Created and started shared RichProgressObserver instance.")
            except ImportError:
                return None  # Rich 不可用或初始化失败
        return _shared_rich_observer_instance

def shutdown_shared_rich_observer():
    """
    停止并清理共享的 RichProgressObserver 实例
    应在应用程序退出时调用
    """
    global _shared_rich_observer_instance
    with _shared_observer_lock:
        if _shared_rich_observer_instance:
            try:
                _shared_rich_observer_instance.stop()
                logger.info("Stopped shared RichProgressObserver instance.")
            except Exception as e:
                logger.error(f"Error stopping shared RichProgressObserver: {e}")
            _shared_rich_observer_instance = None

def create_progress_observer(use_rich: bool = True, 
    shared_mode: bool = True,
    progress_instance: Optional['Progress'] = None,
    console: Optional['Console'] = None
) -> IProgressObserver:
    """
    工厂函数/Singleton 函数：创建适当的进度 observer or returns shared instance
    
    Args:
        use_rich: 是否尝试使用 Rich 库
        shared_mode: 是否使用共享模式（单例）
        progress_instance: 外部 Rich Progress 实例
        console: Rich Console 实例
        
    Returns:
        IProgressObserver 实例
    """
    if use_rich and RICH_AVAILABLE:
        if shared_mode:
            if progress_instance:
                # 如果提供了外部 progress，不使用共享内部观察者，
                # 创建一个包装外部 progress 的实例
                return RichProgressObserver(
                    progress_instance=progress_instance, 
                    console=console, 
                    manage_lifecycle=False
                )
            else:
                # 关键改变：获取共享实例
                observer = get_shared_rich_observer(console=console)
                if observer:
                    return observer
                else:  # 如果共享观察者创建失败，回退到简单模式
                    logger.warning("Failed to get shared Rich observer, falling back to simple.")
                    return SimpleFallbackObserver()
        else:  # 非共享模式，创建新的独立 RichProgressObserver
            return RichProgressObserver(
                progress_instance=progress_instance, 
                console=console, 
                manage_lifecycle=True
            )
    else:
        logger.warning("Using fallback progress observer (simple print)")
        return SimpleFallbackObserver()
