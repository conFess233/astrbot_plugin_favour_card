"""图片与阶段台词的原子持久化，保留旧数据并保护并发修改。"""

import base64
import hashlib
import io
import json
import os
import random
import re
import tempfile
import threading
import time
import uuid
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from .settings import SLOT


def atomic_write(path: Path, content: bytes) -> None:
    """在同目录写临时文件，完成后替换；失败不覆盖原文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def validate_cropped_image(content: bytes, max_bytes: int) -> bytes:
    """校验真实格式、固定像素和单帧，并重新编码以去除元数据。"""
    if not content or len(content) > max_bytes:
        raise ValueError("成品图片为空或超过上传大小限制。")
    try:
        with Image.open(io.BytesIO(content)) as image:
            if image.format not in {"PNG", "WEBP"}:
                raise ValueError("仅接受浏览器导出的 PNG/WebP 成品。")
            if image.size != SLOT or getattr(image, "n_frames", 1) != 1:
                raise ValueError("成品必须是单帧 384 × 600 图片，请重新确认裁剪。")
            image.verify()
        with Image.open(io.BytesIO(content)) as image:
            output = io.BytesIO()
            image.convert("RGBA").save(output, format="PNG", optimize=True)
            result = output.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("图片解码失败，请重新选择有效图片。") from exc
    if len(result) > max_bytes:
        raise ValueError("PNG 成品超过大小限制，请使用较小图片。")
    return result


class PoolStorage:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.images_dir = self.root / "images"
        self.lock = threading.RLock()
        self._image_cache: dict[str, str] = {}

    def _load(self, filename: str, field: str) -> dict:
        path = self.root / filename
        if not path.exists():
            return {
                "schema_version": 1,
                "revision": 0,
                field: [] if field == "images" else {},
            }
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError(
                f"{filename} 已损坏，请从备份恢复；原文件未改动。"
            ) from exc
        if (
            not isinstance(data, dict)
            or data.get("schema_version") != 1
            or type(data.get("revision")) is not int
            or data["revision"] < 0
            or not isinstance(data.get(field), list if field == "images" else dict)
        ):
            raise ValueError(f"{filename} 数据结构不受支持，原文件未改动。")
        return data

    def _save(self, filename: str, data: dict) -> None:
        data["revision"] += 1
        atomic_write(
            self.root / filename,
            json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"),
        )

    def _image_path(self, image_id: str) -> Path:
        if not isinstance(image_id, str) or not re.fullmatch(r"[a-f0-9]{32}", image_id):
            raise ValueError("图片 ID 无效。")
        path = self.images_dir / f"{image_id}.png"
        if path.resolve().parent != self.images_dir.resolve():
            raise ValueError("图片路径越界。")
        return path

    def list_images(self) -> dict:
        with self.lock:
            return self._load("image_pool.json", "images")

    def list_with_thumbnails(self) -> dict:
        with self.lock:
            data = self.list_images()
            for item in data["images"]:
                path = self._image_path(item["id"])
                item["src"] = ""
                item["missing"] = not path.is_file()
                if not item["missing"]:
                    with Image.open(path) as image:
                        image.thumbnail((128, 200))
                        output = io.BytesIO()
                        image.convert("RGBA").save(output, format="PNG")
                    item["src"] = "data:image/png;base64," + base64.b64encode(
                        output.getvalue()
                    ).decode("ascii")
            return data

    def add_image(self, content: bytes, settings: dict) -> dict:
        content = validate_cropped_image(
            content, settings["upload_max_mb"] * 1024 * 1024
        )
        digest = hashlib.sha256(content).hexdigest()
        with self.lock:
            data = self._load("image_pool.json", "images")
            for image in data["images"]:
                if image["sha256"] == digest:
                    return {"image": image, "duplicate": True}
            if len(data["images"]) >= settings["image_pool_limit"]:
                raise ValueError("图片池已达上限，请先删除不需要的图片。")
            image_id = uuid.uuid4().hex
            path = self._image_path(image_id)
            atomic_write(path, content)
            item = {
                "id": image_id,
                "filename": path.name,
                "enabled": True,
                "width": SLOT[0],
                "height": SLOT[1],
                "size": len(content),
                "sha256": digest,
                "uploaded_at": int(time.time()),
                "template": settings["theme"],
            }
            data["images"].append(item)
            try:
                self._save("image_pool.json", data)
            except BaseException:
                path.unlink(missing_ok=True)
                raise
            return {"image": item, "duplicate": False}

    def update_image(self, image_id: str, enabled: bool) -> None:
        self._image_path(image_id)
        if type(enabled) is not bool:
            raise ValueError("启用状态必须是布尔值。")
        with self.lock:
            data = self._load("image_pool.json", "images")
            for image in data["images"]:
                if image["id"] == image_id:
                    image["enabled"] = enabled
                    self._save("image_pool.json", data)
                    self._image_cache.pop(image_id, None)
                    return
            raise ValueError("图片不存在，请刷新列表。")

    def delete_image(self, image_id: str) -> None:
        path = self._image_path(image_id)
        with self.lock:
            data = self._load("image_pool.json", "images")
            items = [image for image in data["images"] if image["id"] != image_id]
            if len(items) == len(data["images"]):
                raise ValueError("图片不存在，请刷新列表。")
            # 先提交清单，确保之后的查询不会选到已删除资源。
            data["images"] = items
            self._save("image_pool.json", data)
            self._image_cache.pop(image_id, None)
            path.unlink(missing_ok=True)

    def image_src(self, image_id: str) -> str:
        with self.lock:
            path = self._image_path(image_id)
            if not any(
                image["id"] == image_id for image in self.list_images()["images"]
            ):
                raise ValueError("图片不存在。")
            if not path.is_file():
                raise ValueError("图片文件缺失，请重新上传。")
            if image_id not in self._image_cache:
                self._image_cache[image_id] = (
                    "data:image/png;base64,"
                    + base64.b64encode(path.read_bytes()).decode("ascii")
                )
            return self._image_cache[image_id]

    def quote_pools(self) -> dict:
        with self.lock:
            return self._load("quote_pools.json", "stages")

    def save_quotes(self, payload: dict, levels: list) -> dict:
        key = payload.get("key")
        level = next((level for level in levels if level.key == key), None)
        if level is None:
            raise ValueError("阶段已变化或不存在，请刷新等级后再保存。")
        raw = payload.get("quotes")
        if not isinstance(raw, list) or len(raw) > 500:
            raise ValueError("每阶段最多保存 500 条台词。")
        quotes, seen = [], set()
        for item in raw:
            if not isinstance(item, dict):
                raise TypeError("台词必须是对象。")
            text, enabled, quote_id = (
                item.get("text"),
                item.get("enabled"),
                item.get("id"),
            )
            if not isinstance(text, str) or not text.strip() or len(text) > 120:
                raise ValueError("每条台词需包含 1～120 字，不能全部为空白。")
            if type(enabled) is not bool:
                raise ValueError("台词启用状态必须是布尔值。")
            if quote_id is None:
                quote_id = uuid.uuid4().hex
            if (
                not isinstance(quote_id, str)
                or not re.fullmatch(r"[a-f0-9]{32}", quote_id)
                or quote_id in seen
            ):
                raise ValueError("台词 ID 无效或重复。")
            seen.add(quote_id)
            quotes.append({"id": quote_id, "text": text, "enabled": enabled})
        revision = payload.get("revision")
        if type(revision) is not int or revision < 0:
            raise ValueError("缺少有效版本，请刷新后重试。")
        with self.lock:
            data = self._load("quote_pools.json", "stages")
            if revision != data["revision"]:
                raise ValueError("台词已被其他页面修改；请保留草稿，刷新后重新合并。")
            previous = data["stages"].get(key)
            if (
                previous
                and (
                    previous.get("display_name") != level.name
                    or previous.get("min") != level.minimum
                    or previous.get("max") != level.maximum
                )
                and payload.get("confirm") is not True
            ):
                raise ValueError("阶段名称或范围已变化，请确认映射后保存。")
            migrate_from = payload.get("migrate_from")
            if migrate_from is not None:
                active = {level.key for level in levels}
                if migrate_from in active or migrate_from not in data["stages"]:
                    raise ValueError("迁移来源不是已失配的旧阶段。")
                if payload.get("confirm") is not True:
                    raise ValueError("请确认迁移范围。")
                # 原阶段保留，可恢复原文；目标修改只在确认后发生。
            data["stages"][key] = {
                "display_name": level.name,
                "min": level.minimum,
                "max": level.maximum,
                "quotes": quotes,
            }
            self._save("quote_pools.json", data)
            return data

    def pick_content(self, settings: dict, level: dict) -> dict:
        """两次独立抽取；不缓存最终卡片，不混用其他阶段的台词。"""
        with self.lock:
            images = (
                [
                    image
                    for image in self.list_images()["images"]
                    if image["enabled"]
                    and image["template"] == settings["theme"]
                    and self._image_path(image["id"]).is_file()
                ]
                if settings["image_pool_enabled"]
                else []
            )
            image = random.choice(images) if images else None
            stage = self.quote_pools()["stages"].get(level["key"], {})
            # 稳定 ID 的阈值或名称变动也需要管理员确认，之前不使用旧文案。
            stage_matches = stage.get("display_name") == level["name"] and (
                level["range"] == f"{stage.get('min')}～{stage.get('max')}"
            )
            quotes = (
                [
                    q
                    for q in stage.get("quotes", [])
                    if q["enabled"] and q["text"].strip()
                ]
                if stage_matches and settings["enable_level_quote"]
                else []
            )
            quote = random.choice(quotes) if quotes else None
            fallback_quote = (
                settings["default_quote"]
                if settings["empty_quote_fallback"] == "default"
                and settings["enable_level_quote"]
                else ""
            )
            return {
                "image_id": image["id"] if image else None,
                "image_src": self.image_src(image["id"]) if image else "",
                "quote_id": quote["id"] if quote else None,
                "quote": quote["text"] if quote else fallback_quote,
            }

    def clear_cache(self) -> None:
        with self.lock:
            self._image_cache.clear()
