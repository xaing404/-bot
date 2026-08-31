import time

from bot.rate_limit import RateLimiter


def test_no_interval():
    rl = RateLimiter(0.0)
    start = time.monotonic()
    rl.wait()
    rl.wait()
    assert time.monotonic() - start < 0.05


def test_min_interval():
    rl = RateLimiter(0.2)
    rl.wait()  # 第一次立即通过
    start = time.monotonic()
    rl.wait()  # 第二次应等待约 0.2s
    elapsed = time.monotonic() - start
    assert elapsed >= 0.15


def test_reset():
    rl = RateLimiter(1.0)
    rl.wait()
    rl.reset()
    start = time.monotonic()
    rl.wait()
    assert time.monotonic() - start < 0.05
