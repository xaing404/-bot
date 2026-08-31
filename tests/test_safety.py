from bot.safety import SafetyFilter


def test_blocked_word():
    f = SafetyFilter(["敏感词", "badword"], enabled=True)
    assert f.is_blocked("这条包含敏感词的消息")
    assert f.is_blocked("contains BADWORD here")


def test_clean_message():
    f = SafetyFilter(["敏感词"], enabled=True)
    assert not f.is_blocked("正常聊天内容")


def test_disabled():
    f = SafetyFilter(["敏感词"], enabled=False)
    assert not f.is_blocked("敏感词")


def test_empty_words():
    f = SafetyFilter([], enabled=True)
    assert not f.is_blocked("任意内容")
