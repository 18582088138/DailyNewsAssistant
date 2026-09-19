"""
配置加载 / Configuration loading.

    from dna.core.config import get_settings, safe_profile, Profile

分两层，各一个文件 / Two layers, one file each:
    `settings.py` —— `.env`：密钥与运行时开关，不进 git
    `profile.py`  —— `config/*.yaml`：订阅源与个人偏好，用户手工调整

**这个 `__init__` 是个刻意的 facade，不是兼容壳。** 全仓四十多处
`from dna.core.config import ...`，而「配置」对调用方本来就是一个整体概念——
让每个调用点去记「这一项在 .env 还是 yaml 里」只会把实现细节泄漏出去。
A deliberate facade: configuration is one concept to its callers, and making each
call site remember which layer a field lives in would leak the split.

密钥只从 `.env` 读取，代码内零硬编码。
Secrets are only ever read from .env; nothing is hard-coded.
"""

from dna.core.config.profile import (
    Profile,
    SourceConfig,
    SourceFilter,
    Tuning,
    load_profile,
    load_sources,
    safe_profile,
)
from dna.core.config.settings import (
    DEFAULT_CONFIG_DIR,
    DEFAULT_ENV_FILE,
    PROJECT_ROOT,
    Settings,
    apply_proxy_env,
    field_default,
    get_settings,
    reload_settings,
)

__all__ = [
    "DEFAULT_CONFIG_DIR",
    "DEFAULT_ENV_FILE",
    "PROJECT_ROOT",
    "Profile",
    "Settings",
    "SourceConfig",
    "SourceFilter",
    "Tuning",
    "apply_proxy_env",
    "field_default",
    "get_settings",
    "load_profile",
    "load_sources",
    "reload_settings",
    "safe_profile",
]
