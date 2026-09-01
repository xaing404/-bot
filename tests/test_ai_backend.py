from bot.ai_backend import AIBackend


def make_cfg(**overrides):
    cfg = {
        "api_key": "test-key",
        "base_url": "http://localhost:1/v1",
        "model": "test-model",
        "timeout": 15,
        "max_retries": 2,
        "retry_backoff": 0,
    }
    cfg.update(overrides)
    return cfg


def test_timeout_configured():
    """必须显式设置请求超时（SDK 默认 600s 会长时间占用线程）。"""
    ai = AIBackend(make_cfg())
    assert ai.timeout == 15


def test_timeout_default():
    ai = AIBackend(make_cfg(timeout=None))
    assert ai.timeout == 30


def test_retry_settings():
    ai = AIBackend(make_cfg())
    assert ai.max_retries == 2
