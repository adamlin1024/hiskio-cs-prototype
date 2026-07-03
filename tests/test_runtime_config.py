"""執行期設定注入測試：白名單、型別過濾、疊加、持久化、預設＝現況。"""
import os
import tempfile

os.environ["RUNTIME_CONFIG_PATH"] = os.path.join(tempfile.gettempdir(), "hibot_rc_test.json")

from core import runtime_config  # noqa: E402


def setup_function():
    runtime_config.reset()
    p = runtime_config._path()
    if p.exists():
        p.unlink()


def test_defaults_when_empty():
    assert runtime_config.get_threshold("max_turns_per_session", 20) == 20
    assert runtime_config.get_prompt_override("cs_response_system") is None


def test_set_threshold_override():
    runtime_config.set_overlay({"thresholds": {"max_turns_per_session": 30}})
    assert runtime_config.get_threshold("max_turns_per_session", 20) == 30


def test_rejects_unknown_and_bad_thresholds():
    runtime_config.set_overlay(
        {"thresholds": {"bogus": 5, "max_off_topic_count": -1, "max_unclear": "x"}}
    )
    assert runtime_config.get_overlay()["thresholds"] == {}


def test_prompt_override_whitelist():
    runtime_config.set_overlay(
        {"prompts": {"cs_response_system": "你是溫柔的客服", "manager_system": "hack"}}
    )
    assert runtime_config.get_prompt_override("cs_response_system") == "你是溫柔的客服"
    # 非白名單的 prompt 不接受（避免亂改決策腦）
    assert runtime_config.get_prompt_override("manager_system") is None


def test_merge_keeps_existing():
    runtime_config.set_overlay({"thresholds": {"max_turns_per_session": 30}})
    runtime_config.set_overlay({"thresholds": {"max_off_topic_count": 9}})
    assert runtime_config.get_threshold("max_turns_per_session", 20) == 30
    assert runtime_config.get_threshold("max_off_topic_count", 3) == 9


def test_persist_and_reload():
    runtime_config.set_overlay({"thresholds": {"max_off_topic_count": 7}})
    runtime_config.reset()  # 清記憶體
    runtime_config.init()   # 從磁碟重載
    assert runtime_config.get_threshold("max_off_topic_count", 3) == 7
