#!/usr/bin/env python3
"""
测试 shared console singleton 方案

这个脚本验证：
1. 所有模块的 logger 都使用同一个 Rich Console
2. 日志输出与进度条正确协调
3. 导入顺序不影响 Rich 配置
"""

import os
import sys
import time
import logging

# 首先导入使用 build_logger 的模块（模拟实际使用场景）
from core.socket_transfer_subject import SocketTransferSubject
from core.utils import build_logger, get_shared_console

# 然后导入 Rich 相关模块
try:
    from core.rich_progress_observer import create_progress_observer
    from rich.progress import Progress, TextColumn, BarColumn
    RICH_AVAILABLE = True
    print("✓ Rich 可用")
except ImportError:
    RICH_AVAILABLE = False
    print("✗ Rich 不可用")

def test_shared_console():
    """测试共享 console 功能"""
    print("\n=== 测试共享 Console Singleton ===")
    
    # 创建多个 logger
    logger1 = build_logger("test.module1")
    logger2 = build_logger("test.module2") 
    logger3 = build_logger("test.module3")
    
    # 获取共享 console
    shared_console = get_shared_console()
    if shared_console:
        print(f"✓ 共享 console 创建成功: {type(shared_console)}")
    else:
        print("✗ 共享 console 创建失败")
        return
    
    # 测试多个 logger 是否都使用 Rich
    print("\n--- 测试多模块日志输出 ---")
    logger1.info("这是来自 module1 的日志消息")
    logger2.warning("这是来自 module2 的警告消息")
    logger3.error("这是来自 module3 的错误消息")
    
    # 测试与进度条的协调
    print("\n--- 测试日志与进度条协调 ---")
    if RICH_AVAILABLE:
        # 创建进度条
        progress = Progress(
            TextColumn("[bold blue]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            console=shared_console
        )
        
        with progress:
            task = progress.add_task("测试进度...", total=100)
            
            for i in range(100):
                # 在进度过程中输出日志
                if i % 20 == 0:
                    logger1.info(f"进度更新: {i}%")
                if i % 33 == 0:
                    logger2.warning(f"中间检查点: {i}")
                
                progress.update(task, advance=1)
                time.sleep(0.05)
                
        logger3.info("进度完成！")
    
    print("\n--- 测试 SocketTransferSubject 日志 ---")
    transfer = SocketTransferSubject()
    # 这应该使用与其他 logger 相同的 Rich console
    transfer_logger = logging.getLogger('core.socket_transfer_subject')
    transfer_logger.info("SocketTransferSubject 日志测试")
    transfer_logger.debug("这是调试信息")
    
    print("\n=== 测试完成 ===")

def test_environment_control():
    """测试环境变量控制"""
    print("\n=== 测试环境变量控制 ===")
    
    # 测试禁用 Rich
    os.environ['USE_RICH_LOGGING'] = 'false'
    
    # 重置并创建新 logger
    from core.utils import reset_shared_console
    reset_shared_console()
    
    logger_no_rich = build_logger("test.no_rich")
    logger_no_rich.info("这应该是标准格式的日志（无 Rich）")
    
    # 恢复 Rich
    os.environ['USE_RICH_LOGGING'] = 'true'
    reset_shared_console()
    
    logger_with_rich = build_logger("test.with_rich")
    logger_with_rich.info("这应该是 Rich 格式的日志")
    
    print("✓ 环境变量控制测试完成")

if __name__ == "__main__":
    test_shared_console()
    test_environment_control()
