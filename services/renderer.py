"""复用宿主 HTML 渲染与端点配置，限制并发和超时。"""

import asyncio
import base64
import io
from pathlib import Path

from astrbot.core.utils.astrbot_path import get_astrbot_temp_path
from PIL import Image

from .domain import resolve_level


class CardRenderer:
    def __init__(self, plugin, storage, settings: dict):
        self.plugin = plugin
        self.storage = storage
        self.slots = asyncio.Semaphore(settings["render_concurrency"])
        self.tasks: set[asyncio.Task] = set()
        self.closed = False
        self.template = ""
        self.fallback = ""

    async def initialize(self) -> None:
        root = Path(__file__).resolve().parent.parent
        self.template, fallback = await asyncio.to_thread(
            lambda: (
                (root / "templates/default.html").read_text(encoding="utf-8"),
                (root / "assets/fallback.png").read_bytes(),
            )
        )
        self.fallback = "data:image/png;base64," + base64.b64encode(fallback).decode(
            "ascii"
        )

    def _build_data(
        self, record, levels: list, settings: dict, preview_image=None, is_preview=False
    ) -> dict:
        level = resolve_level(record.favour, levels)
        content = self.storage.pick_content(settings, level, levels)
        if preview_image:
            content["image_src"] = self.storage.image_src(preview_image)
        if not content["image_src"] and settings["empty_image_fallback"] == "fallback":
            content["image_src"] = self.fallback
        return {
            "username": record.username,
            "user_id": record.user_id,
            "favour": record.favour,
            "relationship": record.relationship,
            "level": level,
            "is_preview": is_preview,
            **settings,
            **content,
        }

    async def render(
        self,
        record,
        levels: list,
        settings: dict,
        preview_image=None,
        *,
        is_preview=False,
    ) -> bytes:
        if self.closed:
            raise ValueError("插件正在停用，请稍后重试。")
        task = asyncio.create_task(
            self._render(record, levels, settings, preview_image, is_preview)
        )
        self.tasks.add(task)
        try:
            return await asyncio.wait_for(task, settings["render_timeout_ms"] / 1000)
        finally:
            self.tasks.discard(task)

    async def _render(
        self, record, levels, settings, preview_image, is_preview
    ) -> bytes:
        async with self.slots:
            data = await asyncio.to_thread(
                self._build_data, record, levels, settings, preview_image, is_preview
            )
            # PNG 不支持 quality；覆盖宿主 JPEG 默认参数。
            path = await self.plugin.html_render(
                self.template,
                data,
                return_url=False,
                options={
                    "type": "png",
                    "quality": None,
                    "animations": "disabled",
                    "scale": "css",
                    "clip": {"x": 0, "y": 0, "width": 960, "height": 600},
                    "timeout": settings["render_timeout_ms"],
                },
            )
            return await asyncio.to_thread(self._read_output, Path(path))

    def _read_output(self, path: Path) -> bytes:
        path = path.resolve()
        root = Path(get_astrbot_temp_path()).resolve()
        if not path.is_relative_to(root):
            raise ValueError("渲染器返回了宿主临时目录之外的路径。")
        try:
            if path.stat().st_size > 8 * 1024 * 1024:
                raise ValueError("渲染成品超过 8 MB，请简化图片素材。")
            content = path.read_bytes()
            with Image.open(io.BytesIO(content)) as image:
                if image.format != "PNG" or image.size != (960, 600):
                    raise ValueError("渲染器未返回 960 × 600 PNG。")
                image.verify()
            return content
        finally:
            path.unlink(missing_ok=True)

    async def close(self) -> None:
        self.closed = True
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.to_thread(self.storage.clear_cache)
