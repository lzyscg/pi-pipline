const test = require("node:test");
const assert = require("node:assert/strict");

const ModelConfig = require("../static/model-config.js");

const catalog = {
  configured_providers: ["opencode", "anthropic"],
  thinking_levels: ["off", "low", "high"],
  models: [
    {
      provider: "opencode",
      model: "deepseek-v4-pro",
      model_id: "opencode/deepseek-v4-pro",
      thinking: true,
      configured: true,
    },
    {
      provider: "opencode",
      model: "deepseek-v4-flash",
      model_id: "opencode/deepseek-v4-flash",
      thinking: true,
      configured: true,
    },
    {
      provider: "anthropic",
      model: "claude-haiku",
      model_id: "anthropic/claude-haiku",
      thinking: false,
      configured: true,
    },
    {
      provider: "unused",
      model: "not-authenticated",
      model_id: "unused/not-authenticated",
      thinking: false,
      configured: false,
    },
  ],
};

test("provider filtering only returns configured models for that provider", () => {
  assert.deepEqual(ModelConfig.providers(catalog), ["opencode", "anthropic"]);
  assert.deepEqual(
    ModelConfig.modelsForProvider(catalog, "opencode").map(item => item.model_id),
    ["opencode/deepseek-v4-pro", "opencode/deepseek-v4-flash"],
  );
});

test("unsupported model forces thinking off", () => {
  assert.deepEqual(
    ModelConfig.thinkingLevelsForModel(catalog, "anthropic/claude-haiku"),
    ["off"],
  );
  assert.deepEqual(
    ModelConfig.thinkingLevelsForModel(catalog, "opencode/deepseek-v4-pro"),
    ["off", "low", "high"],
  );
});

test("three role values become the exact API contract", () => {
  const values = {
    supervisor: { model: "opencode/deepseek-v4-pro", thinking: "high" },
    generator: { model: "opencode/deepseek-v4-flash", thinking: "low" },
    reviewer: { model: "anthropic/claude-haiku", thinking: "off" },
  };

  assert.deepEqual(ModelConfig.agentConfig(values), values);
  assert.throws(
    () => ModelConfig.agentConfig({ supervisor: values.supervisor }),
    /three Agent roles/,
  );
});

test("lane label includes the locked model and thinking", () => {
  assert.equal(
    ModelConfig.laneLabel(
      { model: "opencode/deepseek-v4-pro", thinking: "high" },
      "case",
    ),
    "opencode/deepseek-v4-pro · high",
  );
  assert.equal(
    ModelConfig.laneLabel(
      { model: "opencode/deepseek-v4-pro", thinking: "high" },
      "default",
    ),
    "opencode/deepseek-v4-pro · high · 默认配置",
  );
});
