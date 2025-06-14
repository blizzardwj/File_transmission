#!/usr/bin/env python3
"""
Progress Observer Module

定义进度观察者的抽象接口，用于实现观察者模式的进度更新机制。

Usually, Event subjects are used to notify observers
"""

from abc import ABC, abstractmethod
from typing import List, Optional, Type
from types import TracebackType
from core.progress_events import ProgressEvent

class IProgressObserver(ABC):
    """进度观察者接口"""
    
    @abstractmethod
    def on_event(self, event: ProgressEvent) -> None:
        """
        处理进度事件 handler
        
        Args:
            event: 进度事件对象
        """
        pass

    @abstractmethod
    def __enter__(self) -> 'IProgressObserver':
        """
        Enter the runtime context related to this object.
        The with statement will bind this method's return value to the target(s) specified in the as clause of the statement, if any.
        """
        pass

    @abstractmethod
    def __exit__(self,
        exc_type: Optional[Type[BaseException]],
        exc_value: Optional[BaseException],
        traceback: Optional[TracebackType]
    ) -> Optional[bool]:
        """
        Exit the runtime context related to this object.
        This method is called when the execution of the with statement is finished.
        
        Args:
            exc_type: The exception type raised in the with block, if any
            exc_value: The exception value raised in the with block, if any
            traceback: The traceback object, if any
            
        Returns:
            Optional[bool]: If True, suppresses the exception; otherwise, it propagates
        """
        pass

    @abstractmethod
    def start(self) -> None:
        """
        Start the progress bar instance.
        The progress bar could initialize its internal state or objects and so on.
        """
        pass

    @abstractmethod
    def stop(self) -> None:
        """
        Stop the progress bar instance.
        The progress bar could finalize its internal state or objects and so on.
        """
        pass

    @property
    @abstractmethod
    def has_living_observers(self) -> bool:
        """
        Check if there are any living observers.
        
        Returns:
            bool: True if there are living observers, False otherwise
        """
        pass

class ProgressSubject:
    """
    进度事件发布者 (Subject)，事件主体，负责发布事件
    
    管理观察者列表并发布事件到所有注册的观察者 or listeners.
    """
    
    def __init__(self):
        self._observers: List[IProgressObserver] = []
    
    def add_observer(self, observer: IProgressObserver) -> None:
        """
        添加观察者
        
        Args:
            observer: 实现 IProgressObserver 接口的观察者对象
        """
        if observer not in self._observers:
            self._observers.append(observer)
    
    def remove_observer(self, observer: IProgressObserver) -> None:
        """
        移除观察者
        
        Args:
            observer: 要移除的观察者对象
        """
        if observer in self._observers:
            self._observers.remove(observer)
    
    def notify_observers(self, event: ProgressEvent) -> None:
        """
        通知所有观察者
        
        Args:
            event: 要发送的进度事件
        """
        for observer in self._observers:
            try:
                observer.on_event(event)
            except Exception as e:
                # 避免单个观察者的错误影响其他观察者
                # 这里可以记录日志，但不应该抛出异常
                print(f"Warning: Observer {observer.__class__.__name__} failed to handle event: {e}")
    
    def get_observer_count(self) -> int:
        """获取当前观察者数量"""
        return len(self._observers)
