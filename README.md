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

## 已知限制

- Lite 是本机单用户体验版，不提供鉴权、远程部署或多用户并发。
- 真实 `NEG-PH009-L4` 审核漏检已保留为模型质量失败，不能解释为机制通过。
- 当前硬门不包含参考歌词相似度或版权风险检测；最终歌词仍需人工生产审阅。
