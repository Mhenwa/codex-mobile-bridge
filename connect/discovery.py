"""Local, consent-gated discovery of the active Mhenwa API configuration.

Public discovery never includes credentials. ``select_key`` is intentionally a
separate private operation for local desktop enrollment, not a web endpoint.
The desktop may pass the runtime's config/read result as ``effective_config``;
the file fallback covers the saved global configuration and selected profile.
"""
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

try:
    import tomllib
except ImportError:  # Python 3.9/3.10 desktop bundles
    try:
        import tomli as tomllib
    except ImportError:
        tomllib = None


ALLOWED_ORIGINS = ("https://api.mhenwa.cc", "https://img.mhenwa.cc")


def _url(value):
    if not isinstance(value, str) or len(value) > 2048 or any(c.isspace() for c in value):
        raise ValueError("API 地址无效")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise ValueError("API 地址无效") from None
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment or port not in (None, 443)
            or "\\" in value):
        raise ValueError("仅允许已配置的 Mhenwa HTTPS API 地址")
    origin = "https://" + parsed.hostname.lower()
    return origin, value.rstrip("/")


def _read_config(home, effective_config):
    if effective_config is None:
        if tomllib is None:
            raise ValueError("当前 Python 缺少 TOML 解析器，请安装桌面依赖 tomli")
        try:
            with (home / "config.toml").open("rb") as stream:
                config = tomllib.load(stream)
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            raise ValueError("无法读取 Codex 配置") from None
    else:
        config = effective_config
    if not isinstance(config, dict):
        raise ValueError("Codex 配置无效")
    name = config.get("profile")
    profiles = config.get("profiles") or {}
    profile = profiles.get(name) if isinstance(profiles, dict) and name else {}
    if name and not isinstance(profile, dict):
        raise ValueError("选中的 Codex profile 无效")
    return {**config, **(profile or {})}


def _identity(codex_home, allowed_origins, effective_config, environ, *, resolve=False):
    home = Path(codex_home)
    config = _read_config(home, effective_config)
    provider = config.get("model_provider") or "openai"
    if not isinstance(provider, str) or not 1 <= len(provider) <= 200:
        raise ValueError("Codex provider 无效")
    definitions = config.get("model_providers") or {}
    if not isinstance(definitions, dict):
        raise ValueError("Codex provider 配置无效")
    definition = definitions.get(provider) or {}
    if not isinstance(definition, dict):
        raise ValueError("Codex provider 配置无效")
    base = definition.get("base_url") or (config.get("openai_base_url") if provider == "openai" else None)
    if not base:
        return None
    try:
        origin, base = _url(base)
        allowed = {_url(value)[0] for value in (ALLOWED_ORIGINS if allowed_origins is None else allowed_origins)}
    except ValueError:
        return None
    if origin not in allowed:
        return None
    # Never guess a partial credential for an advanced/custom auth definition.
    if any(definition.get(name) for name in ("http_headers", "env_http_headers", "auth", "gateway_oauth")):
        return None
    if definition.get("wire_api", "responses") != "responses":
        return None
    label = definition.get("name") or provider
    if not isinstance(label, str):
        label = provider
    public = {"id": provider, "provider": provider, "baseUrl": base, "label": label[:200]}
    if not resolve:
        # Detection does not touch auth.json, environment credential values, or
        # native keyring data. Only the later local enrollment consent does.
        return public, None
    env = os.environ if environ is None else environ
    env_name = definition.get("env_key")
    if env_name:
        key = env.get(env_name, "") if isinstance(env_name, str) else ""
    else:
        key = definition.get("experimental_bearer_token") or ""
        if not key and (provider == "openai" or definition.get("requires_openai_auth") is True):
            if config.get("cli_auth_credentials_store") == "keyring":
                return None  # Native keyring retrieval belongs to the desktop runtime.
            try:
                auth = json.loads((home / "auth.json").read_text(encoding="utf-8"))
            except FileNotFoundError:
                auth = {}
            except (OSError, ValueError):
                raise ValueError("无法读取 Codex API 登录配置") from None
            if not isinstance(auth, dict):
                return None
            if auth.get("auth_mode") not in (None, "apikey"):
                return None  # Never reuse a stale API field during native/OAuth login.
            key = auth.get("OPENAI_API_KEY") or env.get("OPENAI_API_KEY", "")
    if (not isinstance(key, str) or not 1 <= len(key) <= 8192
            or any(c.isspace() or ord(c) < 32 for c in key)):
        return None
    return public, key


def discover(codex_home, allowed_origins=None, *, effective_config=None, environ=None):
    """Return the active supported metadata without reading credential values.

    Credential availability is checked only when the user consents to enrollment
    and calls ``select_key``. Discovery must not be used as key validation.
    """
    identity = _identity(codex_home, allowed_origins, effective_config, environ)
    return [identity[0]] if identity else []


def select_key(codex_home, provider_id, allowed_origins=None, *, effective_config=None, environ=None):
    """Resolve the current selected credential locally, re-reading on consent.

    Provider choice is checked again because config may change between discovery
    and enrollment. Keys are never persisted by this helper.
    """
    identity = _identity(codex_home, allowed_origins, effective_config, environ, resolve=True)
    if not identity or identity[0]["id"] != provider_id:
        raise ValueError("选中的 Mhenwa 接入已变化或不可用，请重新检测")
    return identity[1]
