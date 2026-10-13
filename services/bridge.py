"""复用 Favour Ultra 查询与会话语义，仅在记录缺失时初始化。"""

from datetime import datetime
from functools import partial
from importlib import import_module

from astrbot.api import logger
from astrbot.core.star.filter.command import CommandFilter

from .domain import FavourRecord, parse_levels


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

    async def prepare_query_record(
        self, event, record: FavourRecord, settings: dict
    ) -> tuple[FavourRecord, bool]:
        """在原生授权结果之后检查缺失；首次查询标记不会因插入而丢失。"""
        metadata = self.dependency()
        if metadata is None:
            raise ValueError("Favour Ultra 已停用，保留原生回复。")
        instance = metadata.star_cls
        handler = next(
            handler
            for handler in event.get_extra("activated_handlers", [])
            if handler.handler_name == "query_favour"
            and handler.handler_module_path == metadata.module_path
        )
        params = (event.get_extra("handlers_parsed_params", {}) or {}).get(
            handler.handler_full_name, {}
        )
        target = instance._get_target_uid(event, params.get("target", "")) or str(
            event.get_sender_id()
        )
        if str(target) != record.user_id:
            raise ValueError("查询目标与结果不一致，保留原文，不初始化。")
        session_id = instance._get_session_id(event) or "global"
        database = instance.db_manager
        stored = await database.get_favour(record.user_id, session_id)
        missing = stored is None
        if missing and settings["auto_initialize_on_query"]:
            if not settings["initialize_only_if_missing"]:
                raise ValueError("初始化必须保护已有记录。")
            if self.dependency() is not metadata:
                raise ValueError("Favour Ultra 状态已变化，取消初始化。")
            await self._insert_missing(database, record, session_id)
            stored = await database.get_favour(record.user_id, session_id)
            if stored is None:
                raise ValueError("初始化后无法确认记录，请检查 Favour Ultra 日志。")
        if stored is not None:
            relationship = (stored.relationship or "") + (
                " (唯一)" if stored.is_unique else ""
            )
            record = FavourRecord(
                record.username, record.user_id, stored.favour, relationship
            )
            if not self.value_in_range(record.favour):
                raise ValueError("存储值超出 Favour Ultra 范围，保留原文。")
        return record, missing

    @staticmethod
    async def _insert_missing(database, record: FavourRecord, session_id: str) -> None:
        """单条条件插入，兼容新库尚未建立唯一索引的情况，不使用 upsert 更新。"""
        from sqlalchemy import exists, literal, select
        from sqlalchemy.dialects.sqlite import insert

        # shortcut: 使用 Favour Ultra 4.4.7 模型和连接，存储接口变更时须重新核验。
        model = import_module(type(database).__module__).FavourRecord
        table = model.__table__
        now = datetime.now()
        values = {
            "user_id": record.user_id,
            "session_id": session_id,
            "favour": record.favour,
            "relationship": record.relationship,
            "is_unique": False,
            "username": record.username,
            "created_at": now,
            "updated_at": now,
            "last_interaction": now,
        }
        present = exists(
            select(1)
            .select_from(table)
            .where(table.c.user_id == record.user_id, table.c.session_id == session_id)
        )
        selection = select(*(literal(value) for value in values.values())).where(
            ~present
        )
        statement = (
            insert(table).from_select(list(values), selection).on_conflict_do_nothing()
        )
        async with database.engine.begin() as connection:
            await connection.execute(statement)
