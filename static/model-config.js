(function exposeModelConfig(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  } else {
    root.ModelConfig = api;
  }
})(typeof globalThis === "object" ? globalThis : this, function createModelConfig() {
  const roles = ["supervisor", "generator", "reviewer"];

  function providers(catalog) {
    return [...(catalog?.configured_providers || [])];
  }

  function modelsForProvider(catalog, provider) {
    return (catalog?.models || []).filter(
      item => item.configured && item.provider === provider,
    );
  }

  function thinkingLevelsForModel(catalog, modelId) {
    const model = (catalog?.models || []).find(item => item.model_id === modelId);
    return model?.thinking ? [...(catalog?.thinking_levels || ["off"])] : ["off"];
  }

  function agentConfig(values) {
    if (!values || Object.keys(values).sort().join(",") !== [...roles].sort().join(",")) {
      throw new Error("agent_config requires exactly three Agent roles");
    }
    return Object.fromEntries(roles.map(role => {
      const item = values[role];
      if (!item || typeof item.model !== "string" || typeof item.thinking !== "string") {
        throw new Error(`${role} model selection is incomplete`);
      }
      return [role, { model: item.model, thinking: item.thinking }];
    }));
  }

  function laneLabel(config, source) {
    if (!config) return "等待 Case 配置";
    const suffix = source === "default" ? " · 默认配置" : "";
    return `${config.model} · ${config.thinking}${suffix}`;
  }

  return {
    roles,
    providers,
    modelsForProvider,
    thinkingLevelsForModel,
    agentConfig,
    laneLabel,
  };
});
