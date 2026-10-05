"""Discovery selects only active own-provider credentials and never publishes keys."""
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from connect.discovery import discover, select_key


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        (self.home / "auth.json").write_text(json.dumps({"OPENAI_API_KEY": "sk-auth-fixture"}))
        self.config = {"model_provider": "mhenwa", "model_providers": {
            "mhenwa": {"name": "Mhenwa", "base_url": "https://api.mhenwa.cc/v1",
                       "requires_openai_auth": True, "wire_api": "responses"},
            "unused": {"base_url": "https://img.mhenwa.cc/v1", "experimental_bearer_token": "sk-unused-fixture"}}}

    def tearDown(self):
        self.temp.cleanup()

    def discover(self, **kwargs):
        return discover(self.home, effective_config=self.config, environ={}, **kwargs)

    def key(self, provider="mhenwa", **kwargs):
        return select_key(self.home, provider, effective_config=self.config, environ={}, **kwargs)

    def test_active_only_and_public_result_has_no_key(self):
        found = self.discover()
        self.assertEqual(found, [{"id": "mhenwa", "provider": "mhenwa", "baseUrl": "https://api.mhenwa.cc/v1", "label": "Mhenwa"}])
        self.assertNotIn("sk-", json.dumps(found))
        self.assertEqual(self.key(), "sk-auth-fixture")
        with self.assertRaises(ValueError):
            self.key("unused")

    def test_selected_profile_and_runtime_effective_config(self):
        self.config["profile"] = "image"
        self.config["profiles"] = {"image": {"model_provider": "unused"}}
        self.assertEqual(self.discover()[0]["id"], "unused")
        self.assertEqual(self.key("unused"), "sk-unused-fixture")

    def test_environment_key_is_authoritative_no_fallback(self):
        definition = self.config["model_providers"]["mhenwa"]
        definition.update(env_key="FIXTURE_KEY", experimental_bearer_token="sk-inline-fixture")
        self.assertEqual(len(self.discover()), 1)
        with self.assertRaises(ValueError):
            self.key()
        found = discover(self.home, effective_config=self.config, environ={"FIXTURE_KEY": "sk-env-fixture"})
        self.assertNotIn("sk-env-fixture", json.dumps(found))
        self.assertEqual(select_key(self.home, "mhenwa", effective_config=self.config,
                                   environ={"FIXTURE_KEY": "sk-env-fixture"}), "sk-env-fixture")

    def test_foreign_insecure_credentials_queries_and_advanced_auth_denied(self):
        definition = self.config["model_providers"]["mhenwa"]
        for url in ("http://api.mhenwa.cc/v1", "https://foreign.example/v1", "https://api.mhenwa.cc.evil/v1",
                    "https://user:secret@api.mhenwa.cc/v1", "https://api.mhenwa.cc/v1?key=secret",
                    "https://api.mhenwa.cc:8443/v1", "https://api.mhenwa.cc/v1#fragment"):
            definition["base_url"] = url
            with self.subTest(url=url):
                self.assertEqual(self.discover(), [])
        definition["base_url"] = "https://api.mhenwa.cc/v1"
        for name in ("http_headers", "env_http_headers", "auth", "gateway_oauth"):
            definition[name] = {"secret": "sk-fixture"}
            self.assertEqual(self.discover(), [])
            del definition[name]
        definition["wire_api"] = "chat"
        self.assertEqual(self.discover(), [])

    def test_keyring_and_native_login_not_guessed(self):
        self.config["cli_auth_credentials_store"] = "keyring"
        self.assertEqual(len(self.discover()), 1)
        with self.assertRaises(ValueError):
            self.key()
        del self.config["cli_auth_credentials_store"]
        (self.home / "auth.json").write_text(json.dumps({"tokens": {"access_token": "native-secret"}}))
        self.assertEqual(len(self.discover()), 1)
        with self.assertRaises(ValueError):
            self.key()

    def test_native_auth_mode_does_not_reuse_stale_api_field(self):
        (self.home / "auth.json").write_text(json.dumps({"auth_mode": "chatgpt",
            "OPENAI_API_KEY": "sk-stale-fixture", "tokens": {"access_token": "native-secret"}}))
        self.assertEqual(len(self.discover()), 1)
        with self.assertRaises(ValueError):
            self.key()

    def test_discovery_never_reads_auth_or_env_key_before_consent(self):
        with patch.object(Path, "read_text", side_effect=AssertionError("auth read without consent")):
            self.assertEqual(len(self.discover()), 1)

        class NoSecretEnvironment(dict):
            def get(self, name, default=None):
                raise AssertionError("environment secret read without consent")

        self.config["model_providers"]["mhenwa"]["env_key"] = "PRIVATE_ENV_KEY"
        self.assertEqual(len(discover(self.home, effective_config=self.config, environ=NoSecretEnvironment())), 1)

    def test_config_change_after_discovery_fails_closed(self):
        self.assertEqual(len(self.discover()), 1)
        self.config["model_provider"] = "unused"
        with self.assertRaises(ValueError):
            self.key()

    def test_file_global_config_profile_and_openai_override(self):
        (self.home / "config.toml").write_text('''profile="api"
model_provider="openai"
openai_base_url="https://api.mhenwa.cc/v1"
[profiles.api]
model_provider="mhenwa"
[model_providers.mhenwa]
base_url="https://img.mhenwa.cc/v1"
requires_openai_auth=true
''')
        found = discover(self.home, environ={})
        self.assertEqual(found[0]["id"], "mhenwa")
        self.assertEqual(select_key(self.home, "mhenwa", environ={}), "sk-auth-fixture")
        (self.home / "config.toml").write_text('model_provider="openai"\nopenai_base_url="https://api.mhenwa.cc/v1"\n')
        self.assertEqual(discover(self.home, environ={})[0]["id"], "openai")

    def test_saved_files_unchanged_and_missing_config(self):
        before = (self.home / "auth.json").read_bytes()
        self.discover()
        self.key()
        self.assertEqual((self.home / "auth.json").read_bytes(), before)
        self.assertEqual(discover(self.home), [])


if __name__ == "__main__":
    unittest.main()
