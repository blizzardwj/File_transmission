#!/usr/bin/env python3
"""
Progress Events Module

定义文件传输过程中的进度事件类，用于观察者模式的事件驱动进度更新。
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional
import time
from uuid import uuid4


@dataclass
class ProgressEvent:
    """基础进度事件类"""
    task_id: str
    timestamp: float
    data: Dict[str, Any]
    
    def __init__(self, task_id: str, **kwargs):
        self.task_id = task_id
        self.timestamp = time.time()
        self.data = kwargs


class TaskStartedEvent(ProgressEvent):
    """任务开始事件"""
    
    def __init__(self, task_id: str, description: str, total: float, **kwargs):
        super().__init__(task_id, description=description, total=total, **kwargs)
    
    @property
    def description(self) -> str:
        return self.data.get('description', 'Processing...')
    
    @property
    def total(self) -> float:
        return self.data.get('total', 100.0)


class ProgressAdvancedEvent(ProgressEvent):
    """进度推进事件"""
    
    def __init__(self, task_id: str, advance: float, description: Optional[str] = None, **kwargs):
        super().__init__(task_id, advance=advance, description=description, **kwargs)
    
    @property
    def advance(self) -> float:
        return self.data.get('advance', 0.0)
    
    @property
    def description(self) -> Optional[str]:
        return self.data.get('description')


class TaskFinishedEvent(ProgressEvent):
    """任务完成事件"""
    
    def __init__(self, task_id: str, description: Optional[str] = None, success: bool = True, **kwargs):
        super().__init__(task_id, description=description, success=success, **kwargs)
    
    @property
    def description(self) -> Optional[str]:
        return self.data.get('description')
    
    @property
    def success(self) -> bool:
        return self.data.get('success', True)


class TaskErrorEvent(ProgressEvent):
    """任务错误事件"""
    
    def __init__(self, task_id: str, error_message: str, **kwargs):
        super().__init__(task_id, error_message=error_message, **kwargs)
    
    @property
    def error_message(self) -> str:
        return self.data.get('error_message', 'Unknown error')


def generate_task_id() -> str:
    """生成唯一的任务ID"""
    return str(uuid4())[:8]  # 使用UUID的前8位作为简短ID
