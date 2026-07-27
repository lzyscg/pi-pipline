from __future__ import annotations

import tempfile
import unittest
import warnings
from pathlib import Path

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated",
    category=Warning,
)

from fastapi.testclient import TestClient

from app.orchestrator import CaseManager
from app.server import app


MODEL_CATALOG = {
    "models": [
        {
            "provider": "opencode",
            "model": "deepseek-v4-pro",
            "model_id": "opencode/deepseek-v4-pro",
            "thinking": True,
            "configured": True,
        },
        {
            "provider": "opencode",
            "model": "deepseek-v4-flash",
            "model_id": "opencode/deepseek-v4-flash",
            "thinking": True,
            "configured": True,
        },
    ],
    "configured_providers": ["opencode"],
    "thinking_levels": ["off", "minimal", "low", "medium", "high", "xhigh", "max"],
}


class StaticCatalog:
    def snapshot(self):
        return MODEL_CATALOG


class ServerModelSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        app.state.manager = CaseManager(self.temp.name, model_catalog=StaticCatalog())
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def payload(self):
        return {
            "reference_lyrics": "河边洗衣隔岸对歌",
            "golden_line": "我的那个心上人",
            "style": "山歌民歌",
            "requirements": "",
            "forbidden_words": "",
            "max_repairs": 3,
        }

    def test_models_endpoint_returns_catalog_and_defaults_without_secrets(self):
        response = self.client.get("/api/models")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(
            body["defaults"]["generator"]["model"],
            "opencode/deepseek-v4-flash",
        )
        self.assertEqual(body["configured_providers"], ["opencode"])
        self.assertNotIn("secret", response.text.lower())
        self.assertNotIn("api_key", response.text.lower())

    def test_create_case_rejects_unknown_model_before_writing_case(self):
        selection = {
            "supervisor": {"model": "opencode/deepseek-v4-pro", "thinking": "high"},
            "generator": {"model": "missing/unknown", "thinking": "off"},
            "reviewer": {"model": "opencode/deepseek-v4-pro", "thinking": "high"},
        }
        before = list(self.root.iterdir())

        response = self.client.post(
            "/api/cases",
            json={**self.payload(), "agent_config": selection},
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(list(self.root.iterdir()), before)

    def test_create_case_requires_exact_three_role_contract(self):
        response = self.client.post(
            "/api/cases",
            json={
                **self.payload(),
                "agent_config": {
                    "supervisor": {
                        "model": "opencode/deepseek-v4-pro",
                        "thinking": "high",
                    }
                },
            },
        )

        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
