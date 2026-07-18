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
    assert runtime_config.get_threshold("max_off_topic_count", 3) == 3
    assert runtime_config.get_prompt_override("cs_response_system") is None


def test_set_threshold_override():
    runtime_config.set_overlay({"thresholds": {"max_off_topic_count": 7}})
    assert runtime_config.get_threshold("max_off_topic_count", 3) == 7


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
    runtime_config.set_overlay({"thresholds": {"max_unclear": 4}})
    runtime_config.set_overlay({"thresholds": {"max_off_topic_count": 9}})
    assert runtime_config.get_threshold("max_unclear", 2) == 4
    assert runtime_config.get_threshold("max_off_topic_count", 3) == 9


def test_persist_and_reload():
    runtime_config.set_overlay({"thresholds": {"max_off_topic_count": 7}})
    runtime_config.reset()  # 清記憶體
    runtime_config.init()   # 從磁碟重載
    assert runtime_config.get_threshold("max_off_topic_count", 3) == 7


def test_clear_threshold_back_to_default():
    """對抗健檢 2026-07-18：注入 0＝清除 max_off_topic/max_unclear、回退呼叫端預設（修單向旋鈕坑）。"""
    runtime_config.set_overlay({"thresholds": {"max_off_topic_count": 8, "max_unclear": 5}})
    assert runtime_config.get_threshold("max_off_topic_count", 3) == 8
    assert runtime_config.get_threshold("max_unclear", 2) == 5
    # 清空欄位 → HiSupport 推 0 → 這兩個門檻被 pop，get_threshold 回退呼叫端預設
    runtime_config.set_overlay({"thresholds": {"max_off_topic_count": 0, "max_unclear": 0}})
    assert runtime_config.get_threshold("max_off_topic_count", 3) == 3
    assert runtime_config.get_threshold("max_unclear", 2) == 2
    assert "max_off_topic_count" not in runtime_config.get_overlay()["thresholds"]
    assert "max_unclear" not in runtime_config.get_overlay()["thresholds"]


def test_max_daily_zero_is_unlimited_not_cleared():
    """max_daily_messages 的 0 是『明確無上限』語意，照存、不當清除（與另兩個門檻不同）。"""
    runtime_config.set_overlay({"thresholds": {"max_daily_messages": 50}})
    assert runtime_config.get_threshold("max_daily_messages", 0) == 50
    runtime_config.set_overlay({"thresholds": {"max_daily_messages": 0}})
    # 0 照存（get_threshold 回 0＝無上限，呼叫端 _check_daily_quota 以 >0 才啟用判讀）
    assert runtime_config.get_threshold("max_daily_messages", 0) == 0
    assert runtime_config.get_overlay()["thresholds"]["max_daily_messages"] == 0


def test_clear_prompt_and_message_back_to_default():
    """對抗健檢：注入空字串＝清除人設／安撫話、回退檔案預設（修單向旋鈕坑）。"""
    runtime_config.set_overlay({"prompts": {"cs_response_system": "你是溫柔的客服"},
                                "messages": {"handoff_message": "客製安撫話"}})
    assert runtime_config.get_prompt_override("cs_response_system") == "你是溫柔的客服"
    assert runtime_config.get_message("handoff_message", "預設") == "客製安撫話"
    # 清空 → 推空字串 → 移除該 key、回退預設
    runtime_config.set_overlay({"prompts": {"cs_response_system": ""},
                                "messages": {"handoff_message": ""}})
    assert runtime_config.get_prompt_override("cs_response_system") is None
    assert runtime_config.get_message("handoff_message", "預設") == "預設"
