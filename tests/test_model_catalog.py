from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.model_catalog import PiModelCatalog, parse_model_list


MODEL_LIST = """provider     model                   context  max-out  thinking  images
opencode     deepseek-v4-flash       1M       384K     yes       no
opencode     deepseek-v4-pro         1M       384K     yes       no
anthropic    claude-sonnet-4         200K     64K      yes       yes
broken
"""


class ModelCatalogTests(unittest.TestCase):
    def test_parse_model_list_returns_hand_checked_capabilities(self):
        models = parse_model_list(MODEL_LIST)

        self.assertEqual(
            [(item.model_id, item.supports_thinking, item.supports_images) for item in models],
            [
                ("opencode/deepseek-v4-flash", True, False),
                ("opencode/deepseek-v4-pro", True, False),
                ("anthropic/claude-sonnet-4", True, True),
            ],
        )

    def test_snapshot_exposes_provider_name_but_never_auth_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            auth_path = Path(tmp) / "auth.json"
            auth_path.write_text(
                json.dumps(
                    {
                        "opencode": {"type": "api_key", "key": "secret-opencode-value"},
                        "unused": {"type": "api_key", "key": "secret-unused-value"},
                    }
                ),
                encoding="utf-8",
            )
            catalog = PiModelCatalog(
                pi_binary="pi",
                auth_path=auth_path,
                environ={},
                runner=lambda _command: MODEL_LIST,
            )
            snapshot = catalog.snapshot()

        encoded = json.dumps(snapshot, ensure_ascii=False)
        self.assertEqual(snapshot["configured_providers"], ["opencode"])
        self.assertTrue(snapshot["models"][0]["configured"])
        self.assertFalse(snapshot["models"][2]["configured"])
        self.assertNotIn("secret-opencode-value", encoded)
        self.assertNotIn("secret-unused-value", encoded)
        self.assertNotIn(str(auth_path), encoded)

    def test_environment_auth_marks_provider_configured_without_exposing_key(self):
        catalog = PiModelCatalog(
            pi_binary="pi",
            auth_path=Path("/missing/auth.json"),
            environ={"ANTHROPIC_API_KEY": "secret-anthropic-value"},
            runner=lambda _command: MODEL_LIST,
        )

        snapshot = catalog.snapshot()

        self.assertEqual(snapshot["configured_providers"], ["anthropic"])
        self.assertTrue(snapshot["models"][2]["configured"])
        self.assertNotIn("secret-anthropic-value", json.dumps(snapshot))


if __name__ == "__main__":
    unittest.main()
