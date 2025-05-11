import os
import yaml
import logging

"""
统一日志格式和配置
保持模块级别的日志命名
防止重复添加handler
"""
def build_logger(name: str = "project"):
    """Create and configure logger

    Args:
        name: Logger name, defaults to "project". If None, uses module name
    """
    logger = logging.getLogger(name if name else __name__)

    # Only add handler if it doesn't exist
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False

    return logger

logger = build_logger(__name__)

def load_config(config_path: str):
    """
    加载 YAML 格式的配置文件。
    如果未指定 config_path，默认查找上级目录下的 config.yaml。
    返回解析后的字典。
    """
    if config_path is None:
        # config_path = os.path.abspath(
        #     os.path.join(os.path.dirname(__file__), '..', 'config.yaml')
        # )
        raise ValueError("config_path is required")
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"配置文件未找到: {config_path}")
    logger.info(f"加载配置文件: {config_path}")
    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)
