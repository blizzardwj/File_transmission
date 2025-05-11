import os
import yaml

def load_config(config_path=None):
    """
    加载 YAML 格式的配置文件。
    如果未指定 config_path，默认查找上级目录下的 config.yaml。
    返回解析后的字典。
    """
    if config_path is None:
        config_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), '..', 'config.yaml')
        )
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"配置文件未找到: {config_path}")
    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)
