# Pi 歌曲生产对话泳道 Lite

固定展示总控、生成、审核三个真实 Pi Agent 的本地单用户体验版。

本目录是一个独立可运行项目：歌词硬门 Validator 已随 Lite 一并提供，不会读取或
修改外部 Supervisor Runtime。

当前状态：已按 `PLAN.md` 的 P0a → P0b → P1 顺序完成实现。P0a 仅作为
体验检查点记录；自动化退出矩阵、真实 Pi 正常 Case、真实定点返修、冷审和
进程清理均已执行。模型质量缺陷与机制验收分开记录。

## 安全边界

- 服务只允许绑定 `127.0.0.1`。
- 原始运行数据默认写入仓库外的当前用户私有目录，权限必须为 `0700`。
- thinking 属于敏感本机诊断数据，默认折叠且不进入下游 Agent 上下文。
- 不要把运行目录、API Key、Token 或授权头提交到 Git。

## 启动

```bash
python3 -m pip install -r requirements.txt
./start.command
```

默认页面：`http://127.0.0.1:8791`

展开“新建歌词生产任务”后，可在“选择测试案例”中直接选择内置的
`PH-009`、`PH-046`、`PH-094` 或 `PH-168`。这些是可公开发布的精简测试物料，
不依赖原始实验仓库。

### Agent 模型选择

“Agent 模型配置”区域会读取本机 Pi 模型目录。总控、生成、审核 Agent 可分别
选择已配置的提供商、模型和 thinking 等级。创建任务后，这三个选择会固化为
Case 配置并显示在对应泳道头部。

运行中或历史 Case 不允许切换模型：总控和生成需要维持 Case Session 的上下文
一致性，审核虽然每个歌词版本使用新的冷 Session，也会沿用该 Case 锁定的审核
模型。需要比较另一组模型时，请新建生产任务。

## 验证

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/run_real_neg_ph009_l4.py
.venv/bin/python scripts/probe_real_pi_stop.py
```

完整证据见
[`reports/PI歌曲生产对话泳道Lite验收报告.md`](reports/PI歌曲生产对话泳道Lite验收报告.md)。
本次流程与页面分层迭代证据见
[`reports/泳道流程与任务分层迭代验收.md`](reports/泳道流程与任务分层迭代验收.md)。
Case 级模型选择证据见
[`reports/case-agent-model-selection-acceptance.md`](reports/case-agent-model-selection-acceptance.md)。

## 实现范围

- 三个固定泳道分别展示总控、生成和审核 Agent。
- 每个 Agent Turn 聚合成一张轮次卡片，展示业务输入、脱敏模型输入、默认折叠
  thinking、最终输出和相邻轮次路由。
- 主链路为“总控初始化 → 生成 → 审核 → 总控终审”；代码硬门失败和审核打回
  直接返回生成，不再增加无业务价值的总控往返。
- 代码拥有状态、版本、冻结行、允许动作与六项硬门；总控不能越权交付。
- 总控和生成在 Case 内保持 Session；每个歌词版本使用新的审核 Session。
- 最近任务按“生产任务 → 歌词版本轮次”两级显示；Case ID 只作为技术标识。
- JSONL journal 支持 SSE 续传、页面回放、最近 10 个 Case 和 orphaned 启动恢复。
- 支持停止当前 Agent、取消 Case、人工继续、歌词版本、行级 Diff 与最终交付。
- 支持在创建 Case 时分别锁定三个 Agent 的 Pi provider、model 与 thinking；
  选择、实际模型输入事件和 Pi 模型回执可相互核对。

## 已知限制

- Lite 是本机单用户体验版，不提供鉴权、远程部署或多用户并发。
- 真实 `NEG-PH009-L4` 审核漏检已保留为模型质量失败，不能解释为机制通过。
- 当前硬门不包含参考歌词相似度或版权风险检测；最终歌词仍需人工生产审阅。
- 模型目录能判断 Pi 是否配置了 provider 凭证，但不能预知账户剩余额度；额度
  耗尽时仍按现有 known-failed 和 fail-closed 机制停止，不会继续错误流转。
