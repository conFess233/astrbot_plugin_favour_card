"""严格解析原生结果，并按实际等级计算展示数据。"""

import re
from dataclasses import dataclass
from itertools import pairwise

STAGE_PRESETS = {
    "极度厌恶": ("#8d3b44", "你我之间，无话可说。"),
    "反感": ("#797184", "还是保持些距离吧。"),
    "普通": ("#617c84", "有何事？"),
    "喜欢": ("#71b3b3", "若有闲暇，我愿听你说说。"),
    "亲密": ("#2f8f87", "与你相处，倒也自在。"),
    "挚爱": ("#d6b76e", "我既认定了你，便不会轻易改变。"),
}


def validate_color(value: object) -> str:
    """颜色限定六位 HEX，避免把任意 CSS 带入渲染模板。"""
    if not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        raise ValueError("阶段颜色必须是 #RRGGBB 格式，例如 #2f8f87。")
    return value.lower()


@dataclass(frozen=True)
class FavourRecord:
    username: str
    user_id: str
    favour: int
    relationship: str


@dataclass(frozen=True)
class Level:
    key: str
    name: str
    minimum: int
    maximum: int
    color: str = "#111111"


RESULT_PATTERN = re.compile(
    r"\A🔍 用户：([^\r\n]{0,256})\r?\n"
    r"🆔 ID：([0-9]{1,20})\r?\n"
    r"❤ 好感度：(-?[0-9]{1,12})\r?\n"
    r"🔗 关系：([^\r\n]{0,128})\Z"
)


def parse_favour_result(text: str) -> FavourRecord | None:
    """只接受当前受支持的完整四行结果，拒绝错误回复与附加内容。"""
    match = RESULT_PATTERN.fullmatch(text)
    if not match:
        return None
    name, user_id, favour, relationship = match.groups()
    return FavourRecord(name, user_id, int(favour), relationship)


def parse_levels(raw: object) -> list[Level]:
    """校验整数闭区间；配置异常或重叠时由调用方降级。"""
    if not isinstance(raw, list):
        raise TypeError("Favour Ultra 等级配置必须是列表。")
    levels = []
    keys = set()
    for item in raw:
        if not isinstance(item, dict):
            raise TypeError("等级必须是对象。")
        low, high, name = item.get("min"), item.get("max"), item.get("name")
        if type(low) is not int or type(high) is not int or low > high:
            raise ValueError("等级上下限必须是有序整数。")
        if not isinstance(name, str) or not name.strip() or len(name) > 80:
            raise ValueError("等级名称必须是 1～80 字的文本。")
        stable_id = item.get("id")
        if stable_id is not None and (
            not isinstance(stable_id, (str, int)) or isinstance(stable_id, bool)
        ):
            raise ValueError("等级 ID 必须是字符串或整数。")
        key = f"id:{stable_id}" if stable_id is not None else f"{low}:{high}"
        if key in keys:
            raise ValueError("等级键重复。")
        keys.add(key)
        color = STAGE_PRESETS.get(name, ("#111111", ""))[0]
        levels.append(Level(key, name, low, high, color))
    levels.sort(key=lambda level: level.minimum)
    if any(a.maximum >= b.minimum for a, b in pairwise(levels)):
        raise ValueError("等级范围重叠，请先修正 Favour Ultra 配置。")
    return levels


def resolve_level(favour: int, levels: list[Level]) -> dict:
    """返回阶段信息；空档不推断等级，最高阶段和单点区间避免除零。"""
    for index, level in enumerate(levels):
        if level.minimum <= favour <= level.maximum:
            highest = index == len(levels) - 1
            progress = (
                1.0
                if highest or level.minimum == level.maximum
                else (favour - level.minimum) / (level.maximum - level.minimum)
            )
            return {
                "key": level.key,
                "name": level.name,
                "color": level.color,
                "range": f"{level.minimum}～{level.maximum}",
                "progress": round(progress * 100),
                "next_text": "已达到最高阶段"
                if highest
                else f"距下一阶段还差 {levels[index + 1].minimum - favour}",
            }
    return {
        "key": None,
        "name": "未定义等级",
        "color": "#111111",
        "range": "",
        "progress": None,
        "next_text": "当前数值未匹配已定义阶段",
    }
