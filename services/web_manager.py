"""宿主认证下的图片池、阶段台词和模拟卡片预览接口。"""

import asyncio
import base64
import copy
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from astrbot.api import logger
from astrbot.api.web import PluginUploadFile, error_response, json_response, request

from .domain import FavourRecord
from .settings import BOUNDS, DEFAULTS, SLOT, settings_snapshot

PLUGIN_NAME = "astrbot_plugin_favour_card"


class WebManager:
    def __init__(self, plugin):
        self.plugin = plugin
        self.storage = plugin.storage
        self.upload_slots = asyncio.Semaphore(2)
        self.settings_lock = asyncio.Lock()

    def register(self) -> None:
        routes = (
            ("settings/get", self.settings_get, "GET"),
            ("settings/save", self.settings_save, "POST"),
            ("avatar/get", self.avatar_get, "GET"),
            ("avatar/upload", self.avatar_upload, "POST"),
            ("avatar/delete", self.avatar_delete, "POST"),
            ("images/list", self.images_list, "GET"),
            ("images/upload", self.images_upload, "POST"),
            ("images/update", self.images_update, "POST"),
            ("images/delete", self.images_delete, "POST"),
            ("quotes/list", self.quotes_list, "GET"),
            ("quotes/save", self.quotes_save, "POST"),
            ("card/preview", self.card_preview, "GET"),
        )
        for endpoint, handler, method in routes:
            self.plugin.context.register_web_api(
                f"/{PLUGIN_NAME}/{endpoint}",
                self._boundary(handler),
                [method],
                endpoint,
            )

    def _boundary(self, handler):
        async def call():
            # 4.28 Dashboard 使用单一管理账户；不把 Bot 管理员 QQ 号当作网页身份。
            username = request.username
            dashboard = self.plugin.context.get_config().get("dashboard", {})
            if not username:
                return error_response("请先登录 AstrBot Dashboard。", status_code=401)
            if username != dashboard.get("username"):
                return error_response(
                    "仅 Dashboard 管理账户可操作卡片素材。", status_code=403
                )
            try:
                return json_response(await handler())
            except (ValueError, TypeError) as exc:
                return error_response(str(exc), status_code=400)
            except TimeoutError:
                return error_response(
                    "预览渲染超时，请检查网络或所配置的文转图服务。", status_code=504
                )
            except Exception:  # noqa: BLE001 -- API 边界记录堆栈，避免向浏览器泄漏内部信息。
                logger.exception("好感度卡片：管理操作失败。")
                return error_response(
                    "操作失败，请检查日志；未保存的输入请保留。", status_code=500
                )

        return call

    async def _payload(self) -> dict:
        payload = await request.json(default=None)
        if not isinstance(payload, dict):
            raise TypeError("请求必须是 JSON 对象。")
        return payload

    def _settings_state(self) -> dict:
        values = self.plugin.settings()
        revision = hashlib.sha256(
            json.dumps(values, sort_keys=True).encode()
        ).hexdigest()
        return {
            "values": values,
            "revision": revision,
            "requires_reload": values["render_concurrency"]
            != self.plugin.renderer.concurrency,
        }

    async def settings_get(self) -> dict:
        schema = await asyncio.to_thread(
            lambda: json.loads(
                (
                    Path(__file__).resolve().parent.parent / "_conf_schema.json"
                ).read_text(encoding="utf-8")
            )
        )
        for key, (low, high) in BOUNDS.items():
            schema[key].update({"minimum": low, "maximum": high})
        schema["initialize_only_if_missing"]["read_only"] = True
        return {**self._settings_state(), "schema": schema}

    async def settings_save(self) -> dict:
        payload = await self._payload()
        values = payload.get("values")
        if not isinstance(values, dict) or set(values) != set(DEFAULTS):
            raise ValueError("请提交完整的插件配置，不能包含未知字段。")
        validated = settings_snapshot(values)
        async with self.settings_lock:
            state = self._settings_state()
            if payload.get("revision") != state["revision"]:
                raise ValueError("配置已被其他页面修改；请保留草稿，刷新后合并。")
            old = copy.deepcopy(dict(self.plugin.config))
            self.plugin.config.update(validated)
            try:
                await asyncio.to_thread(self.plugin.config.save_config)
            except BaseException:
                self.plugin.config.clear()
                self.plugin.config.update(old)
                raise
            return self._settings_state()

    async def images_list(self) -> dict:
        settings = self.plugin.settings()
        data = await asyncio.to_thread(self.storage.list_with_thumbnails)
        return {
            **data,
            "slot": {"width": SLOT[0], "height": SLOT[1]},
            "limits": {
                key: settings[key]
                for key in ("image_pool_limit", "upload_max_mb", "upload_max_pixels")
            },
            "theme": settings["theme"],
        }

    async def avatar_get(self) -> dict:
        settings = self.plugin.settings()
        return {
            "src": await asyncio.to_thread(self.storage.ranking_avatar),
            "slot": {"width": 256, "height": 256},
            "limits": {
                key: settings[key] for key in ("upload_max_mb", "upload_max_pixels")
            },
        }

    async def avatar_upload(self) -> dict:
        settings = self.plugin.settings()
        async with self.upload_slots:
            upload = (await request.files()).get("file")
            if not isinstance(upload, PluginUploadFile):
                raise TypeError("请选择确认裁剪后的头像。")
            try:
                maximum = settings["upload_max_mb"] * 1024 * 1024
                content = await upload.read(maximum + 1)
                return {
                    "src": await asyncio.to_thread(
                        self.storage.save_ranking_avatar, content, maximum
                    )
                }
            finally:
                await upload.close()

    async def avatar_delete(self) -> dict:
        if (await self._payload()).get("confirm") is not True:
            raise ValueError("请确认重置排行头像。")
        await asyncio.to_thread(self.storage.delete_ranking_avatar)
        return {"src": ""}

    async def images_upload(self) -> dict:
        settings = self.plugin.settings()
        async with self.upload_slots:
            files = await request.files()
            upload = files.get("file")
            if not isinstance(upload, PluginUploadFile):
                raise TypeError("请选择确认裁剪后的成品图片。")
            try:
                content = await upload.read(settings["upload_max_mb"] * 1024 * 1024 + 1)
                return await asyncio.to_thread(
                    self.storage.add_image, content, settings
                )
            finally:
                await upload.close()

    async def images_update(self) -> dict:
        payload = await self._payload()
        await asyncio.to_thread(
            self.storage.update_image, payload.get("id"), payload.get("enabled")
        )
        return {"saved": True}

    async def images_delete(self) -> dict:
        payload = await self._payload()
        if payload.get("confirm") is not True:
            raise ValueError("请确认删除这张图片，删除后不可恢复。")
        await asyncio.to_thread(self.storage.delete_image, payload.get("id"))
        return {"deleted": True}

    async def quotes_list(self) -> dict:
        levels, warning = self.plugin.bridge.levels()
        pools = await asyncio.to_thread(self.storage.quote_pools, levels)
        active_keys = {level.key for level in levels}
        return {
            **pools,
            "levels": [asdict(level) for level in levels],
            "orphan_keys": [key for key in pools["stages"] if key not in active_keys],
            "warning": warning,
        }

    async def quotes_save(self) -> dict:
        payload = await self._payload()
        levels, warning = self.plugin.bridge.levels()
        if warning:
            raise ValueError(warning)
        return await asyncio.to_thread(self.storage.save_quotes, payload, levels)

    async def card_preview(self) -> dict:
        if not self.plugin._ready:
            raise ValueError("插件尚未完成初始化，请稍后重试。")
        raw = request.query.get("favour", "325")
        try:
            favour = int(raw)
        except (ValueError, TypeError) as exc:
            raise ValueError("模拟好感度必须是整数。") from exc
        if not -(10**11) <= favour <= 10**11:
            raise ValueError("模拟数值超出范围。")
        levels, _ = self.plugin.bridge.levels()
        record = FavourRecord("模拟用户 · 非真实查询", "123456", favour, "预览关系")
        content = await self.plugin.renderer.render(
            record,
            levels,
            self.plugin.settings(),
            preview_image=request.query.get("image_id"),
            is_preview=True,
        )
        return {
            "src": "data:image/png;base64," + base64.b64encode(content).decode("ascii"),
            "simulated": True,
        }
