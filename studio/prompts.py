"""Build and validate the persisted design brief for a single wearable helmet."""
import re
import unicodedata


MAX_THEME = 500
PRESETS = {"character": "角色头盔", "mecha": "机甲头盔", "creature": "生物头盔"}
STYLES = {"character": "cartoon", "mecha": "cyberpunk", "creature": "fantasy"}
STRUCTURES = {"auto": "自动匹配", "cap_mask": "帽冠＋前面具", "full_shell": "全包覆头盔"}

_DIRECTIONS = {
    "character": "用大块圆润浮雕塑造角色的帽饰、眉眼或标志性五官，使表情在正视和斜视时都可辨认。",
    "mecha": "用硬表面装甲板、眉部传感器与进气格栅表现主题。零件沿既定穿戴外形排布，面板沟线清晰。",
    "creature": "用生物的头冠、眼眶、短角或鳞片表现主题。凸起的根部宽厚，纹理沿现有曲面连续分布。",
}
_SANTA = re.compile(r"圣诞老人|聖誕老人|圣诞老公公|聖誕老公公|\bsanta\b|\bfather christmas\b", re.I)
_ARMORED_SUBJECT = re.compile(r"机器人|機器人|机甲|機甲|宇航员|太空人|装甲骑士|鋼鐵人|钢铁侠|"
                              r"\b(?:robots?|mech(?:a)?s?|astronauts?|armou?red knights?)\b", re.I)
_CONSTRUCTION = re.compile(
    r"(?P<cap>帽[冠子檐]?[\s+和配连与加、]+(?:前脸|脸部|脸)(?:面具)?|"
    r"帽[冠子檐]?[\s+和配连与加、]*(?:前脸|前|脸部)?面[具罩]|"
    r"露(?:出)?(?:双耳|耳朵|耳部|耳|后脑)|(?:耳部|耳朵|后脑)(?:敞开|开放|外露)|半(?:脸|面)面?具|"
    r"\b(?:cap(?:[_ -]| and )mask|open[_ -]back(?:ed)?|half[_ -]mask)\b)"
    r"|(?P<shell>全(?:包覆|包围|包裹|包头盔|封闭|覆盖|罩式?|盔)|包(?:住|覆|裹)?(?:双耳|耳朵|耳部|后脑)|后脑壳|"
    r"\b(?:full[_ -]shell|full[_ -]face helmet|enclosed helmet)\b)", re.I)
_NEGATION = re.compile(r"(?:不要|不用|不需要|不采用|不做成|避免|不|勿|"
                       r"\bnot(?:\s+(?:a|an))?|\bwithout)\s*$", re.I)
_CONSTRUCTION_PROMPTS = {
    "cap_mask": "穿戴结构为帽冠与前面具连成的一件道具。帽冠像真实帽子自然落在眉上与耳廓上缘的帽线上；"
                "前面具仅覆盖面前，由额头正中的宽眉梁直接与帽冠相连。侧面可看到面具的边缘和空隙。"
                "帽檐以下的左右耳部、太阳穴侧后方和整个后脑向外敞开，留出清楚可见的侧后方负空间和戴入入口。"
                "耳侧和后脑没有围板、头套、后壳或颈壳。不可把帽子与面具拆为两个悬空的物体。",
    "full_shell": "穿戴结构为一体全包覆头盔。外壳从盔顶连续包覆到左右耳部和后脑，前面甲、两侧耳甲与后脑壳"
                  "在下颚附近收边，底部留出戴入入口。侧后方保留实体外壳，眼部位于佩戴者的自然眼线。",
}


def _auto_structure(theme, preset):
    """Prefer stated construction; the last construction cue wins on a conflict."""
    text = unicodedata.normalize("NFKC", theme)
    cue = None
    for match in _CONSTRUCTION.finditer(text):
        open_back = match.lastgroup == "cap"
        if _NEGATION.search(text[:match.start()]):
            open_back = not open_back
        cue = "cap_mask" if open_back else "full_shell"
    if cue:
        return cue
    if _SANTA.search(text) or '面具' in text:
        return "cap_mask"
    if _ARMORED_SUBJECT.search(text):
        return "full_shell"
    return "cap_mask" if preset == "character" else "full_shell"


def make_spec(theme, preset="character", structure="auto"):
    if not isinstance(theme, str):
        raise ValueError("请输入头盔的设计主题。")
    theme = theme.strip()
    if not theme or len(theme) > MAX_THEME:
        raise ValueError(f"设计主题需要 1–{MAX_THEME} 个字符。")
    if any((ord(char) < 32 and char not in "\n\r\t") or 0xD800 <= ord(char) <= 0xDFFF for char in theme):
        raise ValueError("设计主题包含无效字符。")
    if not isinstance(preset, str) or preset not in PRESETS:
        raise ValueError("请选择角色、机甲或生物头盔模板。")
    if not isinstance(structure, str) or structure not in STRUCTURES:
        raise ValueError("请选择自动匹配、帽冠＋前面具或全包覆头盔。")
    resolved = _auto_structure(theme, preset) if structure == "auto" else structure
    sections = [
        f"设计主题：{theme}。将主题做成一件可穿戴 Cosplay 头部道具，按以下穿戴结构独立展示。",
        _CONSTRUCTION_PROMPTS[resolved],
        _DIRECTIONS[preset],
    ]
    if _SANTA.search(theme):
        sections.append("圣诞老人的红色软帽自然坐在头顶，帽尖回贴帽身，白色绒边沿帽檐延伸。"
                        "弧形白眉、宽鼻和卷曲白胡须与前面具相连，胡须塑成宽厚的连续浮雕。")
        if resolved == "cap_mask":
            sections.append("红色只用于帽线以上的帽身。太阳穴和耳朵外侧不围红布或红色侧壳，胡须只在脸前。")
    sections.append("保留清晰的眼线，所有装饰固定于道具，细节做成可打印的实体起伏。空置道具独立展示，"
                    "内部留空，不生成人脸皮肤、人体、假人头模、支撑头架、颈部、肩膀、躯干、底座、地面或散件。")
    return {"theme": theme, "preset": preset, "structure": structure, "resolved_structure": resolved,
            "prompt": "".join(sections), "style": STYLES[preset]}
