import json
import os

from bot.role_cards import RoleCards


def test_inline_card():
    roles = RoleCards({"default": "a", "cards": {"a": {"name": "助手", "prompt": "你是 {bot_name}"}}}, bot_name="xiang")
    assert roles.system_prompt() == "你是 xiang"


def test_json_file_card(tmp_path):
    card_file = tmp_path / "card.json"
    card_file.write_text(json.dumps({
        "role_card": {
            "name": "深红羁绊",
            "system_prompt": "你扮演{{user}}的专属追随者。{{user}}只属于你。"
        }
    }, ensure_ascii=False), encoding="utf-8")

    roles = RoleCards({"default": "y", "cards": {"y": {"file": str(card_file)}}}, bot_name="xiang")
    prompt = roles.system_prompt(user="张三")
    assert "张三的专属追随者" in prompt
    assert "{{user}}" not in prompt


def test_json_direct_prompt_field(tmp_path):
    """兼容无 role_card 包裹、用 prompt 字段的 JSON。"""
    card_file = tmp_path / "card2.json"
    card_file.write_text(json.dumps({"name": "小象", "prompt": "你是{bot_name}"}, ensure_ascii=False), encoding="utf-8")
    roles = RoleCards({"default": "x", "cards": {"x": str(card_file)}}, bot_name="xiang")
    prompt = roles.system_prompt()
    assert prompt.startswith("你是xiang")
    assert "身份保护" in prompt  # JSON 角色卡自动追加最高优先级身份保护指令


def test_json_braces_no_error(tmp_path):
    """JSON 中任意花括号不应导致 format 崩溃。"""
    card_file = tmp_path / "card3.json"
    card_file.write_text(json.dumps({"role_card": {"name": "X", "system_prompt": "规则{1}与{{user}}与{bot_name}"}}, ensure_ascii=False), encoding="utf-8")
    roles = RoleCards({"default": "z", "cards": {"z": {"file": str(card_file)}}}, bot_name="B")
    prompt = roles.system_prompt(user="李四")
    assert prompt.startswith("规则{1}与李四与B")
    assert "身份保护" in prompt  # JSON 角色卡自动追加身份保护指令


def test_missing_card_raises():
    roles = RoleCards({"default": "nope", "cards": {}}, bot_name="B")
    try:
        roles.system_prompt()
        assert False, "应抛出 KeyError"
    except KeyError:
        pass


def test_chinese_key_card(tmp_path):
    """兼容中文键名的角色卡（角色名称/系统提示词）。"""
    card_file = tmp_path / "card_cn.json"
    card_file.write_text(json.dumps({
        "角色名称": "沈知微",
        "系统提示词": "你扮演{{user}}的毒舌同学。"
    }, ensure_ascii=False), encoding="utf-8")
    roles = RoleCards({"default": "c", "cards": {"c": {"file": str(card_file)}}}, bot_name="Bot")
    prompt = roles.system_prompt(user="张三")
    assert "毒舌同学" in prompt
    assert roles.get_card()["name"] == "沈知微"


def test_real_yandere_card_loads():
    """项目里的实际角色卡文件应能正确加载。"""
    if not os.path.exists("角色卡_病娇.json"):
        return  # 文件不在时跳过
    roles = RoleCards({"default": "yandere", "cards": {"yandere": {"file": "角色卡_病娇.json"}}}, bot_name="TedaBot")
    prompt = roles.system_prompt(user="测试者")
    assert "病娇" in prompt
    assert "{{user}}" not in prompt
    assert "测试者" in prompt


def test_real_xiaotang_card_loads():
    """结构化角色卡（林小满）：顶层提示词应被正确加载，且结构完整。"""
    if not os.path.exists("lin_xiaotang_character_card.json"):
        return
    roles = RoleCards({"default": "x", "cards": {"x": {"file": "lin_xiaotang_character_card.json"}}},
                      bot_name="xiang")
    card = roles.get_card()
    assert card["name"] == "林小满"
    assert len(card["prompt"]) > 200  # 精炼提示词已写入，不为空
    prompt = roles.system_prompt(user="张三")
    assert "林小满" in prompt
    assert "{{user}}" not in prompt      # 占位符已渲染
    assert "{bot_name}" not in prompt    # 机器人昵称已渲染
    assert "张三" in prompt


def test_structured_card_without_prompt(tmp_path):
    """无系统提示词的结构化卡应自动合成 prompt（兜底能力）。"""
    card_file = tmp_path / "structured.json"
    card_file.write_text(json.dumps({
        "角色基本信息": {"姓名": "测试角色", "一句话定位": "测试定位"},
        "性格特点": {"外在": {"特质": "好奇心强，爱问为什么"}},
        "语言风格": {"总体原则": "多段短句分段发送"}
    }, ensure_ascii=False), encoding="utf-8")
    roles = RoleCards({"default": "s", "cards": {"s": {"file": str(card_file)}}}, bot_name="B")
    card = roles.get_card()
    assert card["name"] == "测试角色"          # 从嵌套的 角色基本信息.姓名 提取
    assert "测试定位" in card["prompt"]        # 从结构化板块合成
    assert "好奇心强" in card["prompt"]
