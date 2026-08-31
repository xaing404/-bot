import time

from bot.dedup import MessageDedup


def test_first_seen_passes():
    d = MessageDedup()
    assert d.filter_new("a") is True


def test_duplicate_blocked():
    d = MessageDedup()
    assert d.filter_new("a") is True
    assert d.filter_new("a") is False


def test_all_fingerprints_must_be_new():
    d = MessageDedup()
    assert d.filter_new("a", "b") is True
    # 任一指纹命中即拦截（id 重放 / 内容重放均可挡住）
    assert d.filter_new("c", "a") is False
    assert d.filter_new("b") is False


def test_window_expiry():
    d = MessageDedup(window=0.05)
    assert d.filter_new("a") is True
    time.sleep(0.08)
    assert d.filter_new("a") is True  # 过窗后放行


def test_mark_forces_duplicate():
    d = MessageDedup()
    d.mark("sent-content")
    assert d.filter_new("sent-content") is False


def test_capacity_trim():
    d = MessageDedup(capacity=3)
    for i in range(5):
        d.filter_new(f"f{i}")
    assert len(d) <= 3
    # 最旧的指纹已被挤出，可再次登记
    assert d.filter_new("f0") is True
