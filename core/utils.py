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
            if not os.path.exists(self.config_path):
                logger.error(f"Configuration file not found: {self.config_path}")
                sys.exit(1)
                
            with open(self.config_path, 'r') as f:
                self.config = yaml.safe_load(f)
                
            logger.info(f"Configuration loaded from {self.config_path}")
            return self.config
        except Exception as e:
            logger.error(f"Error loading configuration: {e}")
            sys.exit(1)
    
    def validate_config(self) -> bool:
        """
        Validate the configuration
        
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
        
        # Check that at least one mode is enabled
        if not (self.config.get('sender', {}).get('enabled') or 
                self.config.get('receiver', {}).get('enabled')):
            logger.error("Neither sender nor receiver mode is enabled")
            return False
            
        # If sender is enabled, check if file is specified
        if self.config.get('sender', {}).get('enabled'):
            file_path = self.config.get('sender', {}).get('file')
            if not file_path:
                logger.error("Sender mode enabled but no file specified")
                return False
            if not os.path.isfile(file_path):
                logger.error(f"Specified file does not exist: {file_path}")
                return False
                
        # If receiver is enabled, check if output directory is valid
        if self.config.get('receiver', {}).get('enabled'):
            output_dir = self.config.get('receiver', {}).get('output_dir', '.')
            if not os.path.exists(output_dir):
                try:
                    os.makedirs(output_dir)
                    logger.info(f"Created output directory: {output_dir}")
                except Exception as e:
                    logger.error(f"Cannot create output directory: {e}")
                    return False
        
        return True