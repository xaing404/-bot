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
    assert roles.system_prompt() == "你是xiang"


def test_json_braces_no_error(tmp_path):
    """JSON 中任意花括号不应导致 format 崩溃。"""
    card_file = tmp_path / "card3.json"
    card_file.write_text(json.dumps({"role_card": {"name": "X", "system_prompt": "规则{1}与{{user}}与{bot_name}"}}, ensure_ascii=False), encoding="utf-8")
    roles = RoleCards({"default": "z", "cards": {"z": {"file": str(card_file)}}}, bot_name="B")
    assert roles.system_prompt(user="李四") == "规则{1}与李四与B"


def test_missing_card_raises():
    roles = RoleCards({"default": "nope", "cards": {}}, bot_name="B")
    try:
        roles.system_prompt()
        assert False, "应抛出 KeyError"
    except KeyError:
        pass


def test_real_yandere_card_loads():
    """项目里的实际角色卡文件应能正确加载。"""
    if not os.path.exists("角色卡_病娇.json"):
        return  # 文件不在时跳过
    roles = RoleCards({"default": "yandere", "cards": {"yandere": {"file": "角色卡_病娇.json"}}}, bot_name="TedaBot")
    prompt = roles.system_prompt(user="测试者")
    assert "病娇" in prompt
    assert "{{user}}" not in prompt
    assert "测试者" in prompt
