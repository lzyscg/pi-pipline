# Case Agent Model Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users select Pi provider/model/thinking independently for supervisor, generator, and reviewer when creating a Case, then persist and display the immutable Case configuration.

**Architecture:** A focused Pi model catalog module parses `pi --list-models` and exposes only provider names and model capabilities. `CaseManager` validates optional role overrides against that catalog, creates immutable per-Case `RoleProfile` snapshots, persists them, and passes them to `PiStreamRunner`; the browser loads the catalog, submits three role selections, and renders the server-returned snapshot in lane headers.

**Tech Stack:** Python 3.12, FastAPI/Pydantic, asyncio subprocess integration, vanilla HTML/CSS/JavaScript, unittest, Pi CLI 0.81.1.

## Global Constraints

- The system remains isolated under `shan-song-skill-iteration/pi-agent-swimlane-lite/`.
- Do not modify or load the existing Supervisor Runtime.
- Role Skills and Session policies remain server-owned and cannot be changed by the browser.
- A Case model configuration is immutable after creation.
- Supervisor and generator retain Case-persistent Sessions; reviewer retains one cold Session per lyric version.
- No API key, token, auth value, or auth file path may appear in an API response, journal event, page, or committed file.
- Invalid model/provider/thinking selections fail before the Case directory is created.
- Existing Cases without `agent_config` must continue to load using the default profile.
- Each implementation task ends in a scoped Git commit.

---

### Task 1: Pi model catalog and safe public snapshot

**Files:**
- Create: `app/model_catalog.py`
- Create: `tests/test_model_catalog.py`

**Interfaces:**
- Produces: `THINKING_LEVELS: tuple[str, ...]`
- Produces: `parse_model_list(output: str) -> list[ModelOption]`
- Produces: `configured_provider_names(auth_path: Path | None = None, environ: Mapping[str, str] | None = None) -> set[str]`
- Produces: `PiModelCatalog.snapshot() -> dict` containing `models`, `configured_providers`, and `thinking_levels`
- `ModelOption.public(configured_providers: set[str]) -> dict` returns only `provider`, `model`, `model_id`, `thinking`, and `configured`

- [ ] **Step 1: Write failing parser and redaction tests**

```python
class ModelCatalogTests(unittest.TestCase):
    SAMPLE = """provider model context max-out thinking images
opencode deepseek-v4-pro 1M 384K yes no
anthropic claude-sonnet-4 200K 64K yes yes
"""

    def test_parse_model_list_returns_provider_model_and_capabilities(self):
        models = parse_model_list(self.SAMPLE)
        self.assertEqual(models[0].model_id, "opencode/deepseek-v4-pro")
        self.assertTrue(models[0].supports_thinking)

    def test_snapshot_exposes_provider_name_but_never_auth_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            auth = Path(tmp) / "auth.json"
            auth.write_text('{"opencode":{"type":"api_key","key":"secret-value"}}')
            catalog = PiModelCatalog(pi_binary="pi", auth_path=auth, runner=lambda _: self.SAMPLE)
            snapshot = catalog.snapshot()
        encoded = json.dumps(snapshot)
        self.assertIn('"configured": true', encoded)
        self.assertNotIn("secret-value", encoded)
```

- [ ] **Step 2: Run the new tests and verify RED**

Run: `.venv/bin/python -m unittest tests.test_model_catalog -v`

Expected: import failure because `app.model_catalog` does not exist.

- [ ] **Step 3: Implement minimal catalog parsing and configured-provider detection**

Implement immutable `ModelOption`, a six-column whitespace parser that skips headers/malformed lines, auth-provider name extraction from `~/.pi/agent/auth.json`, supported environment-provider mappings, and a subprocess runner for `[pi_binary, "--list-models"]` with a 10-second timeout.

- [ ] **Step 4: Run catalog tests and full suite**

Run: `.venv/bin/python -m unittest tests.test_model_catalog -v`

Expected: all catalog tests pass.

Run: `.venv/bin/python -m unittest discover -s tests`

Expected: all existing tests pass.

- [ ] **Step 5: Commit**

```bash
git add shan-song-skill-iteration/pi-agent-swimlane-lite/app/model_catalog.py \
  shan-song-skill-iteration/pi-agent-swimlane-lite/tests/test_model_catalog.py
git commit -m "增加 Pi 模型目录"
```

### Task 2: Case role-profile validation and immutable persistence

**Files:**
- Modify: `app/config.py`
- Modify: `app/orchestrator.py`
- Modify: `tests/test_core.py`
- Modify: `tests/test_orchestrator.py`

**Interfaces:**
- Produces: `role_profiles_from_selection(base: LiteProfile, raw: dict | None, catalog: dict | None, *, require_available: bool) -> tuple[dict[Role, RoleProfile], str]`
- Produces: `public_role_config(role_profiles: dict[Role, RoleProfile], source: str) -> dict`
- `CaseRuntime.role_profiles` owns the immutable runtime profiles.
- `CaseRuntime.role_config_source` is either `case` or `default`.

- [ ] **Step 1: Write failing validation tests**

```python
def test_role_selection_requires_exact_roles_and_available_models(self):
    with self.assertRaises(RuntimeError):
        role_profiles_from_selection(PROFILE, {"supervisor": {}}, CATALOG, require_available=True)
    with self.assertRaises(RuntimeError):
        role_profiles_from_selection(PROFILE, VALID_SELECTION_WITH_UNKNOWN_MODEL, CATALOG, require_available=True)

def test_role_selection_only_overrides_model_and_thinking(self):
    profiles, source = role_profiles_from_selection(PROFILE, VALID_SELECTION, CATALOG, require_available=True)
    self.assertEqual(source, "case")
    self.assertEqual(profiles["generator"].model, "opencode/deepseek-v4-flash")
    self.assertEqual(profiles["generator"].skill, PROFILE.roles["generator"].skill)
    self.assertTrue(profiles["generator"].persistent_session)
```

- [ ] **Step 2: Run focused tests and verify RED**

Run: `.venv/bin/python -m unittest tests.test_core -v`

Expected: import failure for the new config functions.

- [ ] **Step 3: Implement config snapshot validation**

Require exactly three roles; split `provider/model`; verify `model_id` exists, `configured` is true, thinking belongs to `THINKING_LEVELS`, and thinking is `off` when the catalog says unsupported. Copy Skill and persistence fields only from the base profile. A missing selection clones the base role profiles and returns source `default`.

- [ ] **Step 4: Write failing Case persistence and backward-compatibility tests**

```python
async def test_case_persists_and_restores_role_selection(self):
    case = await manager.create_case({**VALID_INPUT, "agent_config": VALID_SELECTION})
    self.assertEqual(case.public_state()["agent_config"]["generator"]["model"], "opencode/deepseek-v4-flash")
    stored = json.loads((case.case_dir / "input.json").read_text())
    self.assertEqual(stored["agent_config"], case.public_state()["agent_config"])
    reloaded = CaseManager(str(manager.root), model_catalog=FAKE_CATALOG)
    self.assertEqual(reloaded.cases[case.case_id].role_profiles["generator"].model, "opencode/deepseek-v4-flash")

def test_old_case_without_agent_config_uses_default_profiles(self):
    reloaded = CaseManager(str(root), model_catalog=FAKE_CATALOG)
    state = reloaded.cases[case_id].public_state()
    self.assertEqual(state["agent_config_source"], "default")
```

- [ ] **Step 5: Run persistence tests and verify RED**

Run: `.venv/bin/python -m unittest tests.test_orchestrator -v`

Expected: failures because CaseRuntime has no per-Case profiles or public configuration.

- [ ] **Step 6: Implement persistence, public state, recovery, and per-Case provenance**

Add `role_profiles` and `role_config_source` to `CaseRuntime`. Validate before creating `case_dir`. Store the public role snapshot under top-level `agent_config` in `input.json`; include it in `case_created`, `public_state`, snapshots, and a copy of provenance containing `agent_config`. When loading an old Case, clone the default profile without checking the current catalog.

- [ ] **Step 7: Run focused and full tests**

Run: `.venv/bin/python -m unittest tests.test_core tests.test_orchestrator -v`

Expected: all focused tests pass.

Run: `.venv/bin/python -m unittest discover -s tests`

Expected: full suite passes.

- [ ] **Step 8: Commit**

```bash
git add shan-song-skill-iteration/pi-agent-swimlane-lite/app/config.py \
  shan-song-skill-iteration/pi-agent-swimlane-lite/app/orchestrator.py \
  shan-song-skill-iteration/pi-agent-swimlane-lite/tests/test_core.py \
  shan-song-skill-iteration/pi-agent-swimlane-lite/tests/test_orchestrator.py
git commit -m "固化 Case Agent 模型配置"
```

### Task 3: Model catalog API and actual Pi invocation evidence

**Files:**
- Modify: `app/server.py`
- Modify: `app/orchestrator.py`
- Create: `tests/test_server.py`
- Modify: `tests/test_orchestrator.py`

**Interfaces:**
- Produces: `GET /api/models -> {models, configured_providers, thinking_levels, defaults}`
- Extends: `CaseInput.agent_config: dict[str, AgentRoleInput] | None`
- Extends: `actual_model_input.payload` with `model` and `thinking`

- [ ] **Step 1: Write failing endpoint tests**

```python
def test_models_endpoint_returns_catalog_and_defaults(self):
    response = client.get("/api/models")
    self.assertEqual(response.status_code, 200)
    body = response.json()
    self.assertEqual(body["defaults"]["generator"]["model"], "opencode/deepseek-v4-flash")
    self.assertNotIn("key", json.dumps(body).lower())

def test_create_case_rejects_unknown_model_before_writing_case(self):
    before = list(runtime_root.iterdir())
    response = client.post("/api/cases", json={**VALID_INPUT, "agent_config": UNKNOWN_SELECTION})
    self.assertEqual(response.status_code, 409)
    self.assertEqual(list(runtime_root.iterdir()), before)
```

- [ ] **Step 2: Run endpoint tests and verify RED**

Run: `.venv/bin/python -m unittest tests.test_server -v`

Expected: `/api/models` returns 404 and `CaseInput` ignores/rejects the new contract.

- [ ] **Step 3: Implement endpoint and Pydantic request contract**

Add `AgentRoleInput` and `AgentConfigInput` with exactly three required roles and `extra="forbid"`. Return the manager catalog snapshot plus defaults. Translate role-validation errors to HTTP 409 without creating filesystem artifacts.

- [ ] **Step 4: Write failing invocation evidence test**

```python
async def test_invocation_uses_case_profile_and_records_it(self):
    case = await manager.create_case({**VALID_INPUT, "agent_config": VALID_SELECTION})
    await wait_terminal(case)
    self.assertEqual(runner.calls[0]["role_profile"].model, VALID_SELECTION["supervisor"]["model"])
    actual = next(e for e in case.journal.events if e["event_type"] == "actual_model_input")
    self.assertEqual(actual["payload"]["model"], VALID_SELECTION["supervisor"]["model"])
```

- [ ] **Step 5: Run invocation test and verify RED**

Run: `.venv/bin/python -m unittest tests.test_orchestrator -v`

Expected: runner still receives the global profile or evidence lacks model fields.

- [ ] **Step 6: Route invocation through Case profiles and record model metadata**

Change `_invoke_attempts` to use `case.role_profiles[turn.role]`. Record the model and thinking beside Skill and Session ID in `actual_model_input`; do not put credentials or provider auth metadata in events.

- [ ] **Step 7: Run focused and full tests**

Run: `.venv/bin/python -m unittest tests.test_server tests.test_orchestrator -v`

Expected: focused tests pass.

Run: `.venv/bin/python -m unittest discover -s tests`

Expected: full suite passes.

- [ ] **Step 8: Commit**

```bash
git add shan-song-skill-iteration/pi-agent-swimlane-lite/app/server.py \
  shan-song-skill-iteration/pi-agent-swimlane-lite/app/orchestrator.py \
  shan-song-skill-iteration/pi-agent-swimlane-lite/tests/test_server.py \
  shan-song-skill-iteration/pi-agent-swimlane-lite/tests/test_orchestrator.py
git commit -m "接入 Case 模型选择接口"
```

### Task 4: Three-role creation controls and lane model display

**Files:**
- Modify: `static/index.html`
- Modify: `static/app.js`
- Modify: `static/styles.css`

**Interfaces:**
- Consumes: `GET /api/models`
- Sends: `POST /api/cases.agent_config`
- Consumes: `GET /api/cases/{case_id}.agent_config` and `.agent_config_source`

- [ ] **Step 1: Add model configuration markup with semantic selectors**

Add `#agentModelConfig`, `#modelCatalogError`, and per-role controls named `#supervisorProvider`, `#supervisorModel`, `#supervisorThinking` (and corresponding generator/reviewer IDs). Keep the submit button disabled until catalog loading succeeds.

- [ ] **Step 2: Implement catalog loading and dependent selections**

Fetch `/api/ph-cases` and `/api/models` in parallel. Populate provider selects from `configured_providers`, filter model options by provider, apply `defaults`, and force thinking to `off` when the selected model does not support it. Do not read or render credential data.

- [ ] **Step 3: Submit three role selections and render server snapshots**

Add `agent_config` to the create payload. In `renderState`, set each lane header `.agent-model` text and title from `info.agent_config[role].model` plus thinking. Render “默认配置” only when `info.agent_config_source === "default"`.

- [ ] **Step 4: Style compact cards and lane metadata**

Use a three-column grid matching the swimlanes, collapse to one column under the existing mobile breakpoint, preserve the current lane alignment, and truncate long model IDs with ellipsis without hiding the full `title`.

- [ ] **Step 5: Run static and Python validation**

Run: `.venv/bin/python -m unittest discover -s tests`

Expected: all tests pass.

Run: `.venv/bin/python -m py_compile app/*.py tests/*.py`

Expected: exit 0.

Run: `git diff --check`

Expected: no output.

- [ ] **Step 6: Commit**

```bash
git add shan-song-skill-iteration/pi-agent-swimlane-lite/static/index.html \
  shan-song-skill-iteration/pi-agent-swimlane-lite/static/app.js \
  shan-song-skill-iteration/pi-agent-swimlane-lite/static/styles.css
git commit -m "增加三 Agent 模型选择界面"
```

### Task 5: Restart, browser acceptance, real Pi evidence, documentation, and publish

**Files:**
- Modify: `README.md`
- Create: `reports/case-agent-model-selection-acceptance.md`

**Interfaces:**
- Verifies all contracts from Tasks 1-4 against the running service.

- [ ] **Step 1: Document user behavior and immutability**

Add concise README instructions: expand task creation, choose each role model, create the Case, and read the locked selection in lane headers. State that running/history Cases cannot switch models.

- [ ] **Step 2: Restart the Lite server from the feature checkout**

Run: `./start.command`

Expected: startup succeeds on `http://127.0.0.1:8791` and `/api/health` reports the current commit.

- [ ] **Step 3: Browser acceptance**

Verify all three configuration cards load configured providers/models, selecting a provider filters its model list, creating a Case hides edit controls with the composer, and lane headers show exactly the server-returned Case snapshot after refresh.

- [ ] **Step 4: Real Pi smoke Case**

Create one small PH Case through the real page using a non-default permitted role selection. Confirm the first `actual_model_input` event records the selected supervisor model and that the real Pi event stream reports the same provider/model. Stop the Case after evidence if a full lyric production is not required for this feature acceptance; do not describe the smoke as lyric-quality acceptance.

- [ ] **Step 5: Write acceptance evidence**

Record selected role models, Case ID, model catalog response without secrets, relevant event IDs, browser screenshot path, automated test count, and unresolved issues in `reports/case-agent-model-selection-acceptance.md`.

- [ ] **Step 6: Run final verification**

Run: `.venv/bin/python -m unittest discover -s tests`

Expected: zero failures.

Run: `.venv/bin/python -m py_compile app/*.py tests/*.py`

Expected: exit 0.

Run: `git diff --check`

Expected: no output.

- [ ] **Step 7: Commit documentation and evidence**

```bash
git add shan-song-skill-iteration/pi-agent-swimlane-lite/README.md \
  shan-song-skill-iteration/pi-agent-swimlane-lite/reports/case-agent-model-selection-acceptance.md
git commit -m "补充 Agent 模型选择验收证据"
```

- [ ] **Step 8: Publish standalone subtree**

Create a fresh subtree split for `shan-song-skill-iteration/pi-agent-swimlane-lite`, verify the prior standalone commit is its ancestor, and push the split commit to `pi-pipeline/main` without force.
