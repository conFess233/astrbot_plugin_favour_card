"""当前会话排行：只读取原生记录，分页渲染后统一发送。"""

import asyncio
import base64
import io
import math
from urllib.parse import urljoin, urlsplit

import aiohttp
from astrbot.api import logger
from PIL import Image, ImageOps

from .domain import resolve_level, validate_color


def paginate(records, page_size, page=None):
    ordered = sorted(records, key=lambda r: (-r.favour, int(r.user_id)))
    total_pages = math.ceil(len(ordered) / page_size)
    pages = range(total_pages) if page is None else [min(max(page, 1), total_pages) - 1]
    return (
        [
            (
                index + 1,
                index * page_size,
                ordered[index * page_size : (index + 1) * page_size],
            )
            for index in pages
        ]
        if ordered
        else []
    )


def avatar_data(content):
    with Image.open(io.BytesIO(content)) as image:
        if image.width * image.height > 1_000_000:
            raise ValueError("头像像素过大。")
        output = io.BytesIO()
        ImageOps.fit(image.convert("RGB"), (64, 64)).save(output, format="PNG")
    return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode(
        "ascii"
    )


async def fetch_avatar(client, user_id):
    if not str(user_id).isascii() or not str(user_id).isdigit():
        raise ValueError("无效头像 ID。")
    url = f"https://q1.qlogo.cn/g?b=qq&nk={user_id}&s=100"
    async with asyncio.timeout(5):
        for _ in range(4):
            parsed = urlsplit(url)
            host = parsed.hostname or ""
            if parsed.scheme != "https" or not any(
                host == domain or host.endswith("." + domain)
                for domain in ("qlogo.cn", "gtimg.cn", "qpic.cn")
            ):
                raise ValueError("头像重定向不属于 QQ CDN。")
            async with client.get(url, allow_redirects=False) as response:
                if response.status in (301, 302, 303, 307, 308):
                    url = urljoin(url, response.headers["Location"])
                    continue
                response.raise_for_status()
                content = bytearray()
                async for chunk in response.content.iter_chunked(16384):
                    content.extend(chunk)
                    if len(content) > 256 * 1024:
                        raise ValueError("头像过大。")
                return await asyncio.to_thread(avatar_data, content)
    raise ValueError("头像重定向次数过多。")


class RankingRenderer:
    def __init__(self, renderer, storage, bridge):
        self.renderer = renderer
        self.storage = storage
        self.bridge = bridge

    async def render_pages(self, instance, event, records, settings, page=None):
        levels, _ = self.bridge.levels()
        stages = await asyncio.to_thread(self.storage.quote_pools, levels)
        header_avatar = await asyncio.to_thread(self.storage.ranking_avatar)
        pages = paginate(records, settings["ranking_page_size"], page)
        total_pages = math.ceil(len(records) / settings["ranking_page_size"])
        slots = asyncio.Semaphore(4)
        scope = (
            "适配器共享记录"
            if instance.is_global_favour
            else f"群 {event.get_group_id()}"
            if event.get_group_id()
            else "当前私聊会话"
        )
        images = []
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=5)
        ) as client:
            for page_number, offset, batch in pages:

                async def row(record):
                    async with slots:
                        name = str(record.user_id)
                        try:
                            name = (
                                await asyncio.wait_for(
                                    instance._get_user_display_name(
                                        event, record.user_id
                                    ),
                                    5,
                                )
                                or name
                            )
                        except Exception:  # noqa: BLE001 -- 昵称不可用时保留 ID。
                            logger.debug("好感度排行：昵称获取失败，使用 ID。")
                        avatar = self.renderer.fallback
                        try:
                            avatar = await fetch_avatar(client, record.user_id)
                        except Exception:  # noqa: BLE001 -- 网络或图片异常使用占位头像。
                            logger.debug("好感度排行：头像获取失败，使用占位图。")
                        level = resolve_level(record.favour, levels)
                        stage = stages["stages"].get(level["key"], {})
                        if (
                            stage.get("display_name") == level["name"]
                            and level["range"]
                            == f"{stage.get('min')}～{stage.get('max')}"
                        ):
                            level["color"] = validate_color(
                                stage.get("color", level["color"])
                            )
                        return {
                            "name": str(name)[:256],
                            "avatar": avatar,
                            "relationship": str(record.relationship or "")[:128],
                            "unique": record.is_unique,
                            "favour": record.favour,
                            "level": level,
                            "highlight": str(record.user_id)
                            == str(event.get_sender_id()),
                        }

                rows = await asyncio.gather(*(row(record) for record in batch))
                for rank, item in enumerate(rows, offset + 1):
                    item["rank"] = rank
                columns = min(5, len(rows))
                row_count = math.ceil(len(rows) / columns)
                size = (columns * 310 + 80, row_count * 46 + 190)
                data = {
                    "bot_name": settings["bot_name"],
                    "header_avatar": header_avatar,
                    "scope": scope,
                    "total": len(records),
                    "page": page_number,
                    "pages": total_pages,
                    "columns": [
                        rows[i * row_count : (i + 1) * row_count]
                        for i in range(columns)
                    ],
                    "width": size[0],
                    "height": size[1],
                }
                for attempt in range(settings["retry_count"] + 1):
                    try:
                        images.append(
                            await self.renderer.render_ranking(data, settings, size)
                        )
                        break
                    except Exception:  # 只重试当前页渲染，取消不在此捕获。
                        logger.warning(
                            "好感度排行：第 %s 页渲染失败，尝试 %s/%s。",
                            page_number,
                            attempt + 1,
                            settings["retry_count"] + 1,
                        )
                        if attempt == settings["retry_count"]:
                            raise
        return images
