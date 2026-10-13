"""将 Favour Ultra 的授权单人查询转换为图片卡片。"""

from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image, Plain, Reply
from astrbot.api.star import Context, Star
from astrbot.core.pipeline.context_utils import call_event_hook
from astrbot.core.star.star_handler import EventType
from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

from .services.bridge import FavourBridge
from .services.domain import parse_favour_result
from .services.renderer import CardRenderer
from .services.settings import settings_snapshot
from .services.storage import PoolStorage
from .services.web_manager import WebManager

PLUGIN_NAME = "astrbot_plugin_favour_card"


class FavourCard(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.bridge = FavourBridge(context)
        self.storage = PoolStorage(Path(get_astrbot_plugin_data_path()) / PLUGIN_NAME)
        self.renderer = CardRenderer(self, self.storage, self.settings())
        self.web = WebManager(self)
        self.web.register()
        self._ready = False
        self._last_error = None

    def settings(self) -> dict:
        return settings_snapshot(self.config)

    async def initialize(self) -> None:
        await self.renderer.initialize()
        self._ready = True
        _, error = self.bridge.levels()
        if error:
            logger.warning("好感度卡片：%s", error)

    @filter.on_decorating_result(priority=-10)
    async def decorate_query(self, event: AstrMessageEvent):
        """仅在正常查询完成、图片渲染成功后替换结果。"""
        result = event.get_result()
        if (
            not self._ready
            or result is None
            or result.is_llm_result()
            or len(result.chain) != 1
            or not isinstance(result.chain[0], Plain)
            or not self.bridge.query_source(event)
        ):
            return
        try:
            settings = self.settings()
            if not settings["enabled"]:
                return
            groups = settings["allowed_groups"]
            group_id = str(event.get_group_id() or "")
            if group_id and groups and group_id not in groups:
                return
            original = result.chain[0]
            original_text = original.text
            record = parse_favour_result(original_text)
            if record is None or not self.bridge.value_in_range(record.favour):
                return
            record, missing = await self.bridge.prepare_query_record(
                event, record, settings
            )
            if not settings["intercept_query"] or (
                missing and not settings["show_initial_favour_card"]
            ):
                return
            levels, _ = self.bridge.levels()
            content = await self.renderer.render(record, levels, settings)
            # 渲染期间若其他逻辑改变结果，则保留它的最终结果。
            if (
                event.get_result() is result
                and result.chain == [original]
                and original.text == original_text
            ):
                result.chain[:] = [Image.fromBytes(content)]
                result.use_t2i_ = False
                event.set_result(result)
                message_id = getattr(event.message_obj, "message_id", None)
                if (
                    settings["quote_source_message"]
                    and message_id is not None
                    and str(message_id)
                ):
                    result.chain.insert(0, Reply(id=message_id))
                else:
                    # 4.28 会在后续装饰流程追加全局引用，直发确保关闭时不被重新引用。
                    try:
                        await event.send(result.derive(result.chain))
                        await call_event_hook(event, EventType.OnAfterMessageSentEvent)
                    except Exception:  # noqa: BLE001 -- 发送结果可能未知，禁止重复发送或文字重试。
                        logger.exception("好感度卡片：图片发送失败，不自动重试。")
                    finally:
                        event.clear_result()
            self._last_error = None
        except (ValueError, TimeoutError) as exc:
            message = str(exc) or "渲染超时"
            if message != self._last_error:
                logger.warning("好感度卡片：转换失败，保留原文，原因=%s", message)
            self._last_error = message
        except Exception:  # noqa: BLE001 -- 事件边界隔离未知错误并保留原始查询。
            logger.exception("好感度卡片：转换失败，保留原文。")

    async def terminate(self) -> None:
        self._ready = False
        await self.renderer.close()
