"""設定與等級解析測試：等級→(provider, model)、provider 工廠、價目表、快取。"""
import pytest

from core.llm_providers import AnthropicNativeProvider, OpenAICompatProvider
from core.model_config import (
    ModelRegistry,
    build_provider,
    get_pricing,
    load_config,
    missing_api_keys,
    resolve_role,
)

CONFIG = {
    "providers": {
        "anthropic": {"type": "anthropic", "api_key_env": "ANTHROPIC_API_KEY"},
        "openrouter": {
            "type": "openai_compat",
            "base_url": "https://openrouter.ai/api/v1",
            "api_key_env": "OPENROUTER_API_KEY",
        },
    },
    "roles": {
        "reasoning": {"provider": "anthropic", "model": "claude-sonnet-4-6"},
        "fast": {"provider": "anthropic", "model": "claude-haiku-4-5-20251001"},
    },
    "pricing": {
        "claude-sonnet-4-6": {
            "input": 3.0, "output": 15.0, "cache_read": 0.30, "cache_create": 3.75,
        },
    },
}


def test_resolve_role_returns_provider_and_model():
    assert resolve_role("reasoning", CONFIG) == ("anthropic", "claude-sonnet-4-6")
    assert resolve_role("fast", CONFIG) == ("anthropic", "claude-haiku-4-5-20251001")


def test_resolve_unknown_role_raises():
    with pytest.raises(KeyError):
        resolve_role("nope", CONFIG)


def test_build_provider_anthropic():
    p = build_provider("anthropic", CONFIG)
    assert isinstance(p, AnthropicNativeProvider)
    assert p.name == "anthropic"


def test_build_provider_openai_compat_sets_base_url():
    p = build_provider("openrouter", CONFIG)
    assert isinstance(p, OpenAICompatProvider)
    assert p.name == "openrouter"
    assert p._base_url == "https://openrouter.ai/api/v1"


def test_get_pricing_returns_table_or_none():
    assert get_pricing("claude-sonnet-4-6", CONFIG)["input"] == 3.0
    assert get_pricing("unknown-model", CONFIG) is None


def test_registry_caches_provider_instances():
    reg = ModelRegistry(CONFIG)
    p1, m1 = reg.provider_for_role("reasoning")
    p2, m2 = reg.provider_for_role("fast")  # 同一個 anthropic provider
    assert p1 is p2  # 應快取、共用同一實例
    assert m1 == "claude-sonnet-4-6"
    assert m2 == "claude-haiku-4-5-20251001"


def test_missing_api_keys_flags_unset_env(monkeypatch):
    """等級用到的 provider 金鑰沒設 → 點名出來（含用到它的等級）。"""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    cfg = {
        "providers": {"openrouter": {"type": "openai_compat", "base_url": "u", "api_key_env": "OPENROUTER_API_KEY"}},
        "roles": {
            "reasoning": {"provider": "openrouter", "model": "x"},
            "fast": {"provider": "openrouter", "model": "y"},
        },
    }
    missing = missing_api_keys(ModelRegistry(cfg))
    assert len(missing) == 1
    assert missing[0]["env"] == "OPENROUTER_API_KEY"
    assert set(missing[0]["roles"]) == {"reasoning", "fast"}


def test_missing_api_keys_empty_when_all_set(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    cfg = {
        "providers": {"openrouter": {"type": "openai_compat", "base_url": "u", "api_key_env": "OPENROUTER_API_KEY"}},
        "roles": {"fast": {"provider": "openrouter", "model": "y"}},
    }
    assert missing_api_keys(ModelRegistry(cfg)) == []


def test_missing_api_keys_ignores_provider_without_key_env():
    """本地/免金鑰的 provider（沒設 api_key_env）不該被誤報。"""
    cfg = {
        "providers": {"local": {"type": "openai_compat", "base_url": "http://localhost:1234/v1"}},
        "roles": {"fast": {"provider": "local", "model": "m"}},
    }
    assert missing_api_keys(ModelRegistry(cfg)) == []


def test_missing_api_keys_only_checks_used_providers(monkeypatch):
    """有定義但沒有任何 role 用到的 provider，就算沒金鑰也不報。"""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    missing = missing_api_keys(ModelRegistry(CONFIG))  # CONFIG 的 roles 全用 anthropic
    # CONFIG 兩個等級都用 anthropic → 只該報 anthropic，不報沒人用的 openrouter
    envs = {m["env"] for m in missing}
    assert envs == {"ANTHROPIC_API_KEY"}


def test_load_config_reads_shipped_default_file():
    """出貨的 config/models.toml 應可讀，且 reasoning / fast 兩等級都指向已定義的 provider。

    刻意用結構檢查、不寫死模型名——換模型是常態操作，不該每次換都紅。
    """
    cfg = load_config()
    for role in ("reasoning", "fast"):
        assert role in cfg["roles"], f"缺少等級 {role}"
        spec = cfg["roles"][role]
        assert spec["provider"] in cfg["providers"], f"{role} 指向未定義的 provider"
        assert spec["model"], f"{role} 的 model 不可為空"
