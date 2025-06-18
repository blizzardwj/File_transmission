import os
import yaml
import logging
import threading
from typing import Dict, Any, Optional, TYPE_CHECKING
import sys
from pathlib import Path

if TYPE_CHECKING:
    from rich.console import Console

# Global shared console singleton
_shared_console = None
_console_lock = threading.Lock()

def get_shared_console() -> Optional['Console']:
    """获取共享的 Rich Console 实例/singleton
    
    Returns:
        Rich Console 实例，如果 Rich 不可用则返回 None
    """
    global _shared_console
    if _shared_console is None:
        with _console_lock:    # make sure singleton
            if _shared_console is None:  # 双重检查锁定
                try:
                    from rich.console import Console
                    _shared_console = Console()
                except ImportError:
                    _shared_console = None
    return _shared_console

def build_logger(name: str = "project", level=logging.INFO, force_rich: Optional[bool] = None):
    """创建带有自动 Rich 支持的统一 logger
    
    这个函数会自动检测并使用共享的 Rich Console 实例，确保所有日志输出
    都使用相同的 console，从而与进度条和其他 Rich 元素完美协调。

    Args:
        name: Logger name, defaults to "project". If None, uses module name
        level: Logging level, defaults to INFO
        force_rich: 强制启用/禁用 Rich。None = 自动检测
        
    Returns:
        配置好的 logger 实例
    """
    logger = logging.getLogger(name if name else __name__)
    
    # 如果已经有 handler，不要重复添加
    if logger.handlers:
        return logger
    
    # 决定是否使用 Rich
    use_rich = force_rich
    if use_rich is None:
        # 检查环境变量控制
        use_rich = os.environ.get('USE_RICH_LOGGING', 'true').lower() == 'true'
    
    if use_rich:
        shared_console = get_shared_console()
        
        if shared_console:
            # 使用共享的 Rich Console
            try:
                from rich.logging import RichHandler
                # RichHandler deals with data formatting
                handler = RichHandler(
                    console=shared_console,
                    rich_tracebacks=True,
                    # show_path=False,
                    show_time=True
                )
                # Rich 自己处理格式，只需要消息内容
                handler.setFormatter(logging.Formatter("%(message)s"))
                
                logger.addHandler(handler)
                logger.setLevel(level)
                logger.propagate = False
                
                return logger
                
            except ImportError:
                # Rich 导入失败，降级到标准日志
                pass
    
    # 使用标准日志配置（Rich 不可用或被禁用）
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='[%X]'
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False

    return logger

def reset_shared_console():
    """重置共享 console（主要用于测试）"""
    global _shared_console
    with _console_lock:
        _shared_console = None

logger = build_logger(__name__)

class ConfigLoader:
    """
    Handles loading and validating configuration from YAML files
    """
    def __init__(self, config_path: str):
        self.config_path = config_path
        self.config = None
    
    def load_config(self) -> Dict[str, Any]:
        """
        Load configuration from the YAML file
        
        Returns:
            Dict containing the configuration
        """
        try:
            config_p = Path(self.config_path)
            if not config_p.is_file():
                logger.error(f"Configuration file not found: {self.config_path}")
                sys.exit(1)
                
            with open(config_p, 'r') as f:
                self.config = yaml.safe_load(f)
                
            logger.info(f"Configuration loaded from {self.config_path}")
            return self.config
        except Exception as e:
            logger.error(f"Error loading configuration: {e}")
            sys.exit(1)
    
    def validate_config(self) -> bool:
        """
        Validate the configuration in app mode not in test mode
        
        Returns:
            True if configuration is valid, False otherwise
        """
        if not self.config:
            logger.error("No configuration loaded")
            return False
            
        # Check required SSH settings
        required_ssh_fields = ['jump_server', 'jump_user']
        for field in required_ssh_fields:
            if not self.config.get('ssh', {}).get(field):
                logger.error(f"Missing required SSH configuration: {field}")
                return False
        
        sender_enable = self.config.get('sender', {}).get('enabled')
        receiver_enable = self.config.get('receiver', {}).get('enabled')
        
        # Check that both sender and receiver modes are not enabled simultaneously
        if sender_enable and receiver_enable:
            logger.error("Both sender and receiver cannot be enabled at the same time")
            return False
        
        # Check that at least one mode is enabled
        if not (sender_enable or receiver_enable):
            logger.error("Neither sender nor receiver mode is enabled")
            return False
            
        # If sender is enabled, check if file is specified
        if sender_enable:
            file_path = self.config.get('sender', {}).get('file')
            if not file_path:
                logger.error("Sender mode enabled but no file specified")
                return False
            
            # 使用 Path 库展开用户主目录 (~) 并检查文件是否存在
            expanded_path = Path(file_path).expanduser()
            if not expanded_path.is_file():
                logger.error(f"Specified file does not exist: {expanded_path}")
                return False
                
        # If receiver is enabled, check if output directory is valid
        if receiver_enable:
            output_dir = self.config.get('receiver', {}).get('output_dir', '.')
            
            # 使用 Path 库展开用户主目录 (~) 并检查/创建目录
            expanded_dir = Path(output_dir).expanduser()
            if not expanded_dir.exists():
                try:
                    expanded_dir.mkdir(parents=True, exist_ok=True)
                    logger.info(f"Created output directory: {expanded_dir}")
                except Exception as e:
                    logger.error(f"Cannot create output directory: {e}")
                    return False
        
        return True
