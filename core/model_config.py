"""模型分派設定：讀 config/models.toml、把「等級」解析成 (provider, model)、建/快取 provider。

- 純函式 resolve_role / build_provider / get_pricing 吃「設定 dict」，好測試。
- ModelRegistry 持有設定並快取 provider 實例，供門面(llm_client)使用。
- 金鑰只從環境變數讀（設定檔存的是環境變數名），秘密不進設定檔。
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

try:
    import tomllib  # Python 3.11+ 內建
except ModuleNotFoundError:  # Python 3.10 及以下的後備（避免線上 Python 版本較舊時掛掉）
    import tomli as tomllib  # type: ignore

from core.llm_providers import (
    AnthropicNativeProvider,
    LLMProvider,
    OpenAICompatProvider,
)

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "models.toml"


def load_config(path: str | Path | None = None) -> dict:
    p = Path(path) if path else _DEFAULT_CONFIG_PATH
    with open(p, "rb") as f:
        return tomllib.load(f)


def resolve_role(role: str, config: dict) -> tuple[str, str]:
    """等級 → (provider 名稱, model)。未知等級丟 KeyError。"""
    r = config["roles"][role]
    return r["provider"], r["model"]


# role 層可帶的「呼叫參數」白名單(設定檔 [roles.*] 內的額外鍵;白名單外一律忽略)。
_ROLE_PARAM_KEYS = {"reasoning_enabled"}


def resolve_role_params(role: str, config: dict) -> dict:
    """等級 → 呼叫參數 dict(如 {"reasoning_enabled": False});沒設＝空 dict(維持現況)。"""
    r = config["roles"][role]
    return {k: r[k] for k in _ROLE_PARAM_KEYS if k in r}


def build_provider(provider_name: str, config: dict) -> LLMProvider:
    spec = config["providers"][provider_name]
    ptype = spec["type"]

    api_key = None
    env_name = spec.get("api_key_env")
    if env_name:
        api_key = (os.getenv(env_name) or "").strip() or None

    # LLM 呼叫逾時（秒）：provider 可在設定檔覆寫，否則吃 LLM_TIMEOUT_SECONDS 環境變數、預設 60。
    # 環境變數填非數字時不讓整台服務開不了機——退回 60 並記 warning。
    raw_timeout = os.getenv("LLM_TIMEOUT_SECONDS", "60")
    try:
        default_timeout = float(raw_timeout)
    except (TypeError, ValueError):
        logger.warning("LLM_TIMEOUT_SECONDS 非數字(%r)，改用預設 60 秒", raw_timeout)
        default_timeout = 60.0
    timeout = spec.get("timeout", default_timeout)

    if ptype == "anthropic":
        return AnthropicNativeProvider(api_key=api_key, name=provider_name, timeout=timeout)
    if ptype == "openai_compat":
        return OpenAICompatProvider(
            base_url=spec["base_url"],
            api_key=api_key,
            name=provider_name,
            # 直連 OpenAI 官方等不支援 usage.include 的端點，可在設定檔設 cost_from_response=false。
            cost_from_response=spec.get("cost_from_response", True),
            timeout=timeout,
        )
    raise ValueError(f"未知的 provider type: {ptype}")


def get_pricing(model: str, config: dict) -> dict | None:
    return config.get("pricing", {}).get(model)


class ModelRegistry:
    """持有設定 + 快取 provider 實例。同一 provider 只建一次。"""

    def __init__(self, config: dict):
        self._config = config
        self._providers: dict[str, LLMProvider] = {}

    def provider_for_role(self, role: str) -> tuple[LLMProvider, str]:
        provider_name, model = resolve_role(role, self._config)
        if provider_name not in self._providers:
            self._providers[provider_name] = build_provider(provider_name, self._config)
        return self._providers[provider_name], model

    def params_for_role(self, role: str) -> dict:
        """等級的呼叫參數(白名單過濾;如 reasoning_enabled)。"""
        return resolve_role_params(role, self._config)

    def pricing(self, model: str) -> dict | None:
        return get_pricing(model, self._config)

    @property
    def config(self) -> dict:
        return self._config


# ── 模組層預設 registry（延遲載入、可重置）──────────────────────────
_default_registry: ModelRegistry | None = None


def get_registry() -> ModelRegistry:
    global _default_registry
    if _default_registry is None:
        _default_registry = ModelRegistry(load_config())
    return _default_registry


def reset_registry() -> None:
    """設定檔改動後呼叫，強制下次重新載入。"""
    global _default_registry
    _default_registry = None


def validate_model_config(registry=None) -> "ModelRegistry":
    """啟動時檢查：設定檔可載入、且每個 [roles.*] 等級都解析得出 provider+model。

    設計成「壞掉就大聲丟錯」——由 app 啟動時呼叫，讓壞設定在開機階段就被擋下、
    不會流到線上請求（線上請求端另有 try/except 退化為 fallback，見 llm_client.call_role）。
    """
    reg = registry or get_registry()
    roles = list(reg.config.get("roles", {}).keys())
    if not roles:
        raise ValueError("config/models.toml 未定義任何 [roles.*] 等級")
    for role in roles:
        reg.provider_for_role(role)  # 解析不出來（未知 provider、缺 base_url…）會丟錯
    return reg


def missing_api_keys(registry: "ModelRegistry | None" = None) -> list[dict]:
    """回傳「roles 實際會用到、但環境變數沒填(或空)」的 provider 金鑰清單。

    每筆：{"provider": 名稱, "env": 環境變數名, "roles": [用到它的等級]}。
    用途：啟動時「缺金鑰就明講」。沒填金鑰不會讓 build_provider 失敗，而是等到線上
    真的呼叫模型才 401、被 llm_client 靜默退化為罐頭回覆——這函式讓它在開機階段就被看見。
    只檢查「有 role 在用、且設定檔有指定 api_key_env」的 provider（免金鑰/沒人用的不誤報）。
    """
    reg = registry or get_registry()
    config = reg.config

    provider_roles: dict[str, list[str]] = {}
    for role, spec in config.get("roles", {}).items():
        pname = spec.get("provider")
        if pname:
            provider_roles.setdefault(pname, []).append(role)

    missing: list[dict] = []
    providers = config.get("providers", {})
    for pname, used_by in provider_roles.items():
        env_name = providers.get(pname, {}).get("api_key_env")
        if env_name and not (os.getenv(env_name) or "").strip():
            missing.append({"provider": pname, "env": env_name, "roles": used_by})
    return missing
