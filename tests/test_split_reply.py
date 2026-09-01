from bot.wechat_client import split_reply


def test_multiline_split():
    """含回车的回复应拆为多条独立消息。"""
    parts = split_reply("@想~沈知微\n看不清汉字\n还是脑子有病")
    assert parts == ["@想~沈知微", "看不清汉字", "还是脑子有病"]


def test_blank_lines_filtered():
    parts = split_reply("第一句\n\n\n第二句\n   \n第三句")
    assert parts == ["第一句", "第二句", "第三句"]


def test_single_line():
    assert split_reply("只有一句") == ["只有一句"]


def test_empty():
    assert split_reply("") == [""]
    assert split_reply("\n\n  \n") == [""]
    assert split_reply(None) == [""]


def test_parts_stripped():
    parts = split_reply("  前后空格  \n\t 第二条 \t")
    assert parts == ["前后空格", "第二条"]
