"""只读 Favour Ultra 实例，并核对宿主实际匹配的查询入口。"""

from functools import partial

from astrbot.api import logger
from astrbot.core.star.filter.command import CommandFilter

from .domain import parse_levels


class FavourBridge:
    def __init__(self, context):
        self.context = context
        self._last_level_error = None

    def dependency(self):
        """根据真实插件身份查找实例，不访问其数据库或配置文件。"""
        for metadata in self.context.get_all_stars():
            if (
                (metadata.name or "").lower() == "astrbot_plugin_favour_ultra"
                and metadata.activated
                and metadata.star_cls is not None
            ):
                return metadata
        return None

    def query_source(self, event) -> bool:
        """复用宿主指令匹配（含前缀、别名和 @ 参数），拒绝竞争指令。"""
        metadata = self.dependency()
        if metadata is None:
            return False
        handlers = event.get_extra("activated_handlers", []) or []
        commands = [
            handler
            for handler in handlers
            if any(isinstance(item, CommandFilter) for item in handler.event_filters)
        ]
        if len(commands) != 1:
            return False
        handler = commands[0]
        bound = handler.handler
        owner = (
            bound.args[0]
            if isinstance(bound, partial) and bound.args
            else getattr(bound, "__self__", None)
        )
        # shortcut: 4.28 没有逐条结果来源标记，升级宿主提供来源后改用来源校验。
        return (
            handler.handler_module_path == metadata.module_path
            and handler.handler_name == "query_favour"
            and owner is metadata.star_cls
            and handler.enabled
        )

    def levels(self) -> tuple[list, str | None]:
        """每次读取运行实例的当前配置，异常只隐藏等级，不编造值。"""
        metadata = self.dependency()
        if metadata is None:
            return [], "请安装并启用 Favour Ultra；素材管理仍可使用。"
        try:
            raw = metadata.star_cls.favour_levels
            levels = parse_levels(raw)
        except (AttributeError, ValueError, TypeError) as exc:
            error = str(exc)
            if error != self._last_level_error:
                logger.warning("好感度卡片：等级配置不可用，原因=%s", error)
            self._last_level_error = error
            return [], "等级配置不可用，请检查 Favour Ultra 的阶段定义。"
        self._last_level_error = None
        return levels, None

    def value_in_range(self, value: int) -> bool:
        metadata = self.dependency()
        if metadata is None:
            return False
        instance = metadata.star_cls
        low = getattr(instance, "min_favour_value", None)
        high = getattr(instance, "max_favour_value", None)
        return type(low) is int and type(high) is int and low <= value <= high
