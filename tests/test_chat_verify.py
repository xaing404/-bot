"""窗口身份校验测试：防止主窗口模式下 ChatWith 静默失败导致跨群消息归属错乱。"""

from bot.wechat_client import chat_info_matches


class TestChatInfoMatches:
    def test_exact_match(self):
        assert chat_info_matches({"chat_name": "黑色星期"}, "黑色星期")

    def test_match_with_member_suffix(self):
        # 微信窗口标题可能带成员数后缀
        assert chat_info_matches({"chat_name": "谁也没我们 (12)"}, "谁也没我们")

    def test_match_ignores_whitespace(self):
        assert chat_info_matches({"chat_name": "黑 色 星 期"}, "黑色星期")

    def test_mismatch(self):
        # 窗口显示的是另一个群 → 校验失败（本轮跳过读取，防归属错乱）
        assert not chat_info_matches({"chat_name": "谁也没我们"}, "黑色星期")

    def test_empty_info(self):
        assert not chat_info_matches({}, "黑色星期")
        assert not chat_info_matches(None, "黑色星期")

    def test_checks_all_values(self):
        # 目标名出现在任一字段值中都算匹配
        assert chat_info_matches({"who": "黑色星期", "type": "group"}, "黑色星期")

    def test_non_string_values(self):
        assert chat_info_matches({"chat_name": "黑色星期", "count": 12}, "黑色星期")
        assert not chat_info_matches({"count": 12}, "黑色星期")
