from bot.matcher import contains_keyword, detect_at, fuzzy_match, match_keywords, strip_mentions


class TestStripMentions:
    def test_remove_at_self(self):
        assert strip_mentions("@TedaBot 你好") == "你好"

    def test_remove_all_mentions(self):
        assert strip_mentions("@李四 来聊天 @TedaBot") == "来聊天"

    def test_keep_plain_text(self):
        assert strip_mentions("你好呀") == "你好呀"

    def test_email_also_stripped(self):
        # 简单策略：@后跟非空白一律视为提及清除
        assert "@" not in strip_mentions("联系我 a@b.com")

    def test_empty(self):
        assert strip_mentions("") == ""
        assert strip_mentions(None) == ""


class TestExactMatch:
    def test_hit(self):
        assert contains_keyword("你好小特，早上好", "小特")

    def test_case_insensitive(self):
        assert contains_keyword("TEDA 在吗", "teda")

    def test_miss(self):
        assert not contains_keyword("今天天气不错", "小特")


class TestFuzzyMatch:
    def test_fuzzy_hit(self):
        # 相似词（机械人 vs 机器人）达到阈值
        assert fuzzy_match("小特机械人上线了", "小特机器人", 0.75)

    def test_fuzzy_miss(self):
        assert not fuzzy_match("完全无关的一句话", "小特机器人", 0.75)

    def test_match_keywords_fuzzy(self):
        assert match_keywords("小特机械人你好", ["小特机器人"], fuzzy=True) == "小特机器人"

    def test_match_keywords_exact_priority(self):
        assert match_keywords("小特你好", ["小特", "小特机器人"], fuzzy=True) == "小特"

    def test_no_match(self):
        assert match_keywords("今天天气不错", ["小特"], fuzzy=False) is None


class TestDetectAt:
    def test_at_prefix(self):
        assert detect_at("@TedaBot 你好", "TedaBot")

    def test_at_middle(self):
        assert detect_at("你好 @TedaBot 看看", "TedaBot")

    def test_no_at(self):
        assert not detect_at("小特你好", "TedaBot")

    def test_empty(self):
        assert not detect_at("", "TedaBot")
        assert not detect_at("你好", "")
