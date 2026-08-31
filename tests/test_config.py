from main import load_config


def test_config_loads():
    cfg = load_config("config.yaml")
    assert cfg["bot"]["name"]
    assert "base_url" in cfg["ai"] and "api_key" in cfg["ai"]
    assert isinstance(cfg["wechat"]["group_whitelist"], list)
    assert isinstance(cfg["trigger"]["keywords"], list)
    assert cfg["roles"]["default"] in cfg["roles"]["cards"]


def test_config_path_argument():
    cfg = load_config("config.yaml")
    assert cfg["logging"]["file"]
