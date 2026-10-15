"""配置默认值与业务边界校验。"""

DEFAULTS = {
    "enabled": True,
    "intercept_query": True,
    "quote_source_message": True,
    "bot_name": "bot",
    "ranking_page_size": 80,
    "retry_count": 3,
    "show_initial_favour_card": True,
    "auto_initialize_on_query": True,
    "initialize_only_if_missing": True,
    "theme": "default",
    "character_name": "角色名",
    "image_pool_enabled": True,
    "show_user_id": False,
    "show_relationship": True,
    "show_progress": True,
    "enable_level_quote": True,
    "image_pool_limit": 50,
    "upload_max_mb": 10,
    "upload_max_pixels": 24000000,
    "empty_image_fallback": "fallback",
    "empty_quote_fallback": "hide",
    "default_quote": "",
    "allowed_groups": [],
    "render_timeout_ms": 10000,
    "render_concurrency": 2,
}
SLOT = (384, 600)
BOUNDS = {
    "ranking_page_size": (1, 80),
    "retry_count": (0, 10),
    "image_pool_limit": (1, 200),
    "upload_max_mb": (1, 20),
    "upload_max_pixels": (230400, 40000000),
    "render_timeout_ms": (1000, 60000),
    "render_concurrency": (1, 4),
}


def settings_snapshot(config: dict) -> dict:
    """旧配置缺项采用默认值，不修改宿主原始配置。"""
    result = {key: config.get(key, value) for key, value in DEFAULTS.items()}
    for key, default in DEFAULTS.items():
        value = result[key]
        if isinstance(default, bool) and type(value) is not bool:
            raise ValueError(f"{key} 必须是布尔值。")
        if key in BOUNDS:
            low, high = BOUNDS[key]
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"{key} 必须在 {low}～{high} 之间。")
    if not result["initialize_only_if_missing"]:
        raise ValueError(
            "initialize_only_if_missing 必须开启，初始化不能覆盖已有记录。"
        )
    for key, limit in (
        ("character_name", 40),
        ("bot_name", 40),
        ("default_quote", 120),
    ):
        if not isinstance(result[key], str) or len(result[key]) > limit:
            raise ValueError(f"{key} 必须是最多 {limit} 字的文本。")
    if not result["character_name"].strip():
        raise ValueError("角色名称不能为空。")
    if not result["bot_name"].strip():
        raise ValueError("Bot 名称不能为空。")
    for key, choices in (
        ("theme", {"default"}),
        ("empty_image_fallback", {"fallback", "hide"}),
        ("empty_quote_fallback", {"hide", "default"}),
    ):
        if not isinstance(result[key], str) or result[key] not in choices:
            raise ValueError(f"{key} 的选项无效。")
    groups = result["allowed_groups"]
    if not isinstance(groups, list) or any(
        not isinstance(group, str) or not group.isascii() or not group.isdigit()
        for group in groups
    ):
        raise ValueError("allowed_groups 必须是群号字符串列表。")
    result["allowed_groups"] = list(groups)
    return result
