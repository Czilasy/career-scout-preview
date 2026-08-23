"""平台注册表存取（021 B7 自 platforms.py 搬运）。

_REGISTRY 经 webui.platforms 门面在调用时动态取用，保持 patch 面不变。
"""

from __future__ import annotations



from webui.platforms_schema import (
    DEFAULT_PLATFORM,
    KNOWN_PLATFORM_KEYS,
    PlatformNotRegisteredError,
    PlatformRegistry,
    UnknownPlatformError,
)




# ---------------------------------------------------------------------------
# 注册表存储
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, PlatformRegistry] = {}




def register_platform(registry: PlatformRegistry) -> None:
    """注册或替换一个平台注册项。"""
    from webui import platforms as _facade
    if not isinstance(registry, PlatformRegistry):
        raise TypeError("registry 必须为 PlatformRegistry")
    if registry.key not in KNOWN_PLATFORM_KEYS:
        raise UnknownPlatformError(f"未知平台键: {registry.key}")
    _facade._REGISTRY[registry.key] = registry




def is_known_platform_key(key: str | None) -> bool:
    """平台键是否在规格允许的已知集合中（不论是否已注册）。"""
    return key in KNOWN_PLATFORM_KEYS




def validate_platform_key(key: str | None) -> str:
    """校验平台键已知；返回规范化键，未知抛 UnknownPlatformError。"""
    if key is None or key == "":
        raise UnknownPlatformError("平台键不能为空")
    if not isinstance(key, str):
        raise UnknownPlatformError(f"平台键必须为字符串，实际类型: {type(key).__name__}")
    if key not in KNOWN_PLATFORM_KEYS:
        raise UnknownPlatformError(f"未知平台键: {key}")
    return key




def get_platform(key: str) -> PlatformRegistry:
    """获取已注册平台；未知抛 UnknownPlatformError，已知未注册抛 PlatformNotRegisteredError。"""
    from webui import platforms as _facade
    validate_platform_key(key)
    reg = _facade._REGISTRY.get(key)
    if reg is None:
        raise PlatformNotRegisteredError(
            f"平台 {key} 已知但尚未注册（真实 fixture/页面合同未核验）"
        )
    return reg




def get_platform_or_none(key: str) -> PlatformRegistry | None:
    """获取已注册平台；未知或未注册返回 None。"""
    from webui import platforms as _facade
    if not is_known_platform_key(key):
        return None
    return _facade._REGISTRY.get(key)




def list_platforms() -> tuple[PlatformRegistry, ...]:
    """列出全部已注册平台（按 KNOWN_PLATFORM_KEYS 顺序）。"""
    from webui import platforms as _facade
    return tuple(
        _facade._REGISTRY[k] for k in KNOWN_PLATFORM_KEYS if k in _facade._REGISTRY
    )




def list_platform_keys() -> tuple[str, ...]:
    """列出全部已注册平台键（按 KNOWN_PLATFORM_KEYS 顺序）。"""
    from webui import platforms as _facade
    return tuple(k for k in KNOWN_PLATFORM_KEYS if k in _facade._REGISTRY)




def resolve_platform_or_default(key: str | None) -> str:
    """解析平台键；None/空 → DEFAULT_PLATFORM；未知抛 UnknownPlatformError。"""
    if key is None or key == "":
        return DEFAULT_PLATFORM
    return validate_platform_key(key)
