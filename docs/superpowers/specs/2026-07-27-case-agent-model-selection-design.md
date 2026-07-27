# Case 级 Agent 模型选择设计

## 目标

在新建歌词生产任务时，允许用户分别为总控、生成和审核 Agent 选择 Pi 已配置的提供商、模型与 thinking 等级。选择结果在 Case 创建时固化，后续所有轮次、返修和冷审都使用同一份 Case 配置。

## 已确认的产品规则

- 三类 Agent 独立配置 `provider/model` 与 thinking 等级。
- 默认值来自 `profiles/mountain-song.json`，不配置时保持现有行为。
- 只允许从本机 Pi 模型目录中选择；不接受任意手输模型标识。
- 配置在创建 Case 时固化。运行中和历史 Case 不提供修改入口。
- 总控、生成继续维持 Case 级持久 Session；审核仍为每个歌词版本新建 Session，但每个审核 Session 使用 Case 固化的审核模型。
- 泳道头展示当前 Case 每个 Agent 的实际模型与 thinking，状态仍单独显示。
- 旧 Case 没有模型快照时使用默认 Profile，并在页面标记为默认配置。

## 方案比较

### 方案 A：Case 级服务端模型目录与快照（采用）

后端读取 `pi --list-models`，向前端提供结构化模型目录；创建 Case 时校验并持久化三类 Agent 配置。运行时从 Case 快照取配置。

优点是选择可验证、历史可复现、Session 语义稳定，且页面展示与真实调用一致。代价是需要同时修改接口、持久化、编排器和前端。

### 方案 B：前端直接传任意模型字符串

实现较快，但模型名拼写错误或供应商未配置时只能在运行中失败，且无法证明页面选择就是实际调用配置，因此不采用。

### 方案 C：只提供全局三 Agent 设置

实现简单，但修改后会影响所有新旧任务，无法比较不同 Case 的模型表现，也会破坏历史追溯，因此不采用。

## 架构

### 模型目录

新增模型目录模块，执行 Pi CLI 的 `--list-models` 并解析以下字段：

- `provider`
- `model`
- `model_id`，格式为 `provider/model`
- `thinking` 是否受支持
- `configured`，表示本机 Pi 当前具备该 provider 的认证配置

`GET /api/models` 返回目录、默认三 Agent 配置和允许的 thinking 等级。响应不包含 API Key、Token、认证内容或认证文件路径。Pi CLI 不可用或目录解析失败时，接口 fail closed，创建面板显示错误，不允许提交无法验证的新配置。

### Case 输入和快照

`POST /api/cases` 新增可选字段：

```json
{
  "agent_config": {
    "supervisor": {"model": "opencode/deepseek-v4-pro", "thinking": "high"},
    "generator": {"model": "opencode/deepseek-v4-flash", "thinking": "high"},
    "reviewer": {"model": "opencode/deepseek-v4-pro", "thinking": "high"}
  }
}
```

后端要求角色集合严格等于 `supervisor/generator/reviewer`，模型必须存在于目录且 provider 已配置，thinking 必须合法；不支持 thinking 的模型只能使用 `off`。

创建成功后，将完整角色配置写入 Case 的 `input.json`、`case_created` 事件、公开状态与 `provenance.json`。模型配置不进入歌词业务提示词。

### 运行时

`CaseRuntime` 持有自己的三个 `RoleProfile`。Pi 调用从 `case.role_profiles[role]` 读取模型、thinking、Skill 和 Session 策略，不再从 `CaseManager` 的全局 Profile 读取模型。

Skill 与 Session 策略仍由默认 Profile 控制，用户只能覆盖模型和 thinking，不能通过接口更改 Skill、持久 Session 或审核冷启动规则。

恢复历史 Case 时：

- 存在 `agent_config`：恢复并再次做结构校验，但不因后来模型目录变化而丢弃历史记录。
- 不存在 `agent_config`：复制默认 Profile 作为兼容快照。
- 只有新 Case 创建时要求模型当前可用且 provider 已配置。

### 前端

“新建歌词生产任务”增加一个默认展开的“Agent 模型配置”区域，包含三张紧凑配置卡：

- Agent 名称与 Session 规则
- 提供商下拉框
- 模型下拉框，随提供商筛选
- thinking 下拉框

选择测试案例只填充歌词物料，不重置模型配置。提交时一并发送 `agent_config`。

泳道头的副标题改为两行：第一行显示 Session 规则，第二行显示实际 `provider/model · thinking`。历史 Case 使用默认回退时显示“默认配置”。窄屏下允许模型文字省略并通过 `title` 查看完整值。

## 数据流

1. 页面启动时并行获取 PH 案例、模型目录和最近任务。
2. 模型目录填充三类 Agent 的默认选择。
3. 用户创建 Case，后端重新校验选择。
4. 后端固化 Case 配置，写入输入元数据、事件和 provenance。
5. 每次 Agent 调用读取 Case 固化配置，并在 `actual_model_input` 事件记录模型与 thinking。
6. 页面从 Case 公开状态渲染三个泳道头，保证展示值来自服务端快照。

## 错误处理

- Pi 模型目录不可读取：创建面板显示“模型目录不可用”，提交按钮禁用。
- 模型不存在、provider 未配置、角色缺失或 thinking 非法：返回 422/409，并且不创建 Case 目录。
- 模型调用时发生额度或认证错误：沿用当前 known-failed 重试和 fail-closed 流程，错误状态不会继续路由。
- 已创建 Case 的模型配置不可通过继续接口或前端修改。

## 测试与验收

自动化覆盖：

- Pi 模型列表解析与认证信息脱敏。
- 合法三角色配置被接受并固化。
- 缺角色、未知模型、未配置 provider、非法 thinking 均在创建前拒绝。
- `PiStreamRunner` 对三个角色使用 Case 快照，而不是全局默认。
- Case 重载保留模型配置；旧 Case 回退默认 Profile。
- 公开状态、`case_created`、`actual_model_input` 和 provenance 均包含一致的模型标识。

页面验收：

- 三个 Agent 都能独立选择 provider、model 和 thinking。
- 新建任务后，三个泳道头展示所选配置。
- 刷新页面和重启服务后显示不变。
- 运行中的 Case 没有修改模型入口。
- 使用真实 Pi 创建一个 Case，关键输入事件中的模型与页面显示一致。

## 非目标

- 不在运行中的 Case 内热切换模型。
- 不在页面管理、写入或展示 API Key。
- 不修改现有 Supervisor Runtime。
- 不允许用户修改 Agent Skill、系统提示词或 Session 策略。
