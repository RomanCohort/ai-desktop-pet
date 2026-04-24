"""安全配置 - API Key 安全存储，优先使用系统密钥环"""
import os

from ..utils.logger import get_logger

_logger = get_logger(__name__)


class SecureConfig:
    """管理敏感配置的安全存取。

    优先级：
    1. 环境变量 OC_API_KEY
    2. 系统密钥环 (keyring)
    3. settings.json 中的 api_key（兼容旧配置）
    """

    SERVICE_NAME = "oc_desktop_pet"
    API_KEY_ENV = "OC_API_KEY"

    def __init__(self, settings: dict):
        self.settings = settings

    def get_api_key(self) -> str:
        """获取 API Key，按优先级从多个来源读取。"""
        # 1. 环境变量
        env_key = os.environ.get(self.API_KEY_ENV, "").strip()
        if env_key:
            return env_key

        # 2. 系统密钥环
        try:
            import keyring
            key = keyring.get_password(self.SERVICE_NAME, "api_key")
            if key:
                return key
        except ImportError:
            _logger.debug("keyring 模块未安装，跳过密钥环读取")
        except Exception as e:
            _logger.debug("密钥环读取失败: %s", e)

        # 3. settings.json（兼容旧配置）
        return self.settings.get("api_key", "").strip()

    def set_api_key(self, key: str) -> None:
        """保存 API Key 到密钥环，并从 settings.json 中移除明文。"""
        key = key.strip()
        if not key:
            return

        # 尝试保存到密钥环
        saved_to_keyring = False
        try:
            import keyring
            keyring.set_password(self.SERVICE_NAME, "api_key", key)
            saved_to_keyring = True
            _logger.info("API Key 已保存到系统密钥环")
        except ImportError:
            _logger.debug("keyring 模块未安装，回退到 settings.json")
        except Exception as e:
            _logger.warning("密钥环写入失败，回退到 settings.json: %s", e)

        # 如果密钥环保存成功，从 settings 中移除
        if saved_to_keyring:
            self.settings.pop("api_key", None)
        else:
            # 密钥环不可用时，回退到 settings.json
            self.settings["api_key"] = key

    def has_secure_storage(self) -> bool:
        """检查密钥环是否可用。"""
        try:
            import keyring
            keyring.get_keyring()  # 检查是否有可用的后端
            return True
        except Exception as e:
            _logger.debug("密钥环不可用: %s", e)
            return False

    def migrate_from_settings(self) -> bool:
        """将 settings.json 中的明文 API Key 迁移到密钥环。

        Returns:
            True 如果迁移成功或无需迁移，False 如果迁移失败。
        """
        current_key = self.settings.get("api_key", "").strip()
        if not current_key or not current_key.startswith("sk-"):
            return True  # 无需迁移

        try:
            import keyring
            keyring.set_password(self.SERVICE_NAME, "api_key", current_key)
            self.settings.pop("api_key", None)
            _logger.info("API Key 已从 settings.json 迁移到密钥环")
            return True
        except ImportError:
            _logger.debug("keyring 模块未安装，无法迁移")
            return False
        except Exception as e:
            _logger.warning("API Key 迁移到密钥环失败: %s", e)
            return False
