# Pi 歌曲生产对话泳道 Lite

固定展示总控、生成、审核三个真实 Pi Agent 的本地单用户体验版。

当前状态：正在按 `PLAN.md` 的 P0a → P0b → P1 顺序实施。P0a 只能称为
体验检查点；P0b 退出矩阵全部通过后才可称为 Lite 完成。

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

