#!/usr/bin/env python3
"""
Progress Observer Module

定义进度观察者的抽象接口，用于实现观察者模式的进度更新机制。
"""

from abc import ABC, abstractmethod
from typing import List
from .progress_events import ProgressEvent

class IProgressObserver(ABC):
    """进度观察者接口"""
    
    @abstractmethod
    def on_event(self, event: ProgressEvent) -> None:
        """
        处理进度事件
        
        Args:
            event: 进度事件对象
        """
        pass

class ProgressSubject:
    """
    进度事件发布者 (Subject)
    
    管理观察者列表并发布事件到所有注册的观察者
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
