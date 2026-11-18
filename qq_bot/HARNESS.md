# QQ 机器人 DeepSeek Harness 运行架构

2026-10-24：聊天主循环使用官方 `@deepseek-ai/dsh@0.2.1-rc.2`，不再让模型输出自定义 `action/continue` JSON 来驱动主聊天。旧循环保留为显式回退路径，不在 Harness 失败时自动重复发送。

## 组成

- `agent_turn.py`：统一入口；`QQ_HARNESS_MODE=on` 进入新路径，`test` 仅放行 `test:private:`，`off` 使用旧路径。
- `harness_runtime.py`：启动官方 SDK / runtime，提供每轮随机认证的 loopback 网关。聊天、工具次数、消息数、授权会话都由程序绑定；模型不能选择收件人。
- `harness_mcp.py`：stdio MCP 工具适配器，提供 `say`、`send_memes`、`search_web`、`manga_link`。
- `harness_protocol.py`：将官方 runtime 的 Messages 请求转为现有预算接口的 Chat Completions，保留 reasoning、tool call id 和工具结果；将结果转回 Messages SSE。
- `api_budget/site_budget.py`：所有模型调用共用原有全站/个人账本。工具 schema、调用参数和回放 reasoning 也计入保守预算。

SDK 使用官方源码 `22ddee3792402c3e70ec5f86a63c3e3e895a2f60` 的 `python/sdk/src/deepseek_harness`；该版本文档中的 PyPI SDK 在部署时未查到，使用 `dsh_bin` 指向已发布 npm runtime，经真实工具循环验证兼容。SDK 源码按原样安装在独立运行目录，未伪造官方包。

## 发送与记忆

模型调用 MCP → Python 业务工具 → 异步发送队列 → QQ 发送及回执 → 队列确认 → 写入 delivery → 工具成功结果回到 Harness。

失败不会记为成功。发送目标由调用方固定，工具没有 group_id/user_id 参数。每轮默认最多 3 条消息；明确要求 4–8 张时扩大到指定数量。多图逐张去重、确认，模型会看到实际成功数量。已通过工具发送后，不额外发送 Harness 最终收尾句，避免“发完又报一遍完成”。

跨轮记忆继续存于原 SQLite，读取最近聊天与确认记录；确认记录增加 turn_id，用于区分刚才一轮与历史累计。每次激活是独立 Harness session，官方 JSONL 轨迹保存到 `QQ_HARNESS_HOME/turns`。这不是无界长期记忆，也不自动恢复上轮的未完成发送。

## 限制与兼容

- 每轮最多 10 次主模型调用、12 次工具调用、3 个不同网页查询；同一查询缓存。文字 1200 字以内，图片请求 1–8 张。
- 关闭 runtime 默认 shell、文件读取、工作目录工具、资源浏览、自动重试和额外会话上传插件；仅开放 QQ MCP 工具。
- 现有漫画号、web/zip/img 命令仍由原业务代码处理；漫画缓存、密码登录、QQ 文件上传流程保留。
- 人设分别取原来的 `SYSTEM` 和 `persona_chen.txt`。部署必须保留服务器用户修改的 SYSTEM。
- 免 @ 频率、群权限、只采集来源群、定时晚安和机器人互聊限制留在接入层。
- Harness 属于开发预览，版本固定；不能在未验证情况下直接更新到 latest。

## 部署

运行目录 `/home/myl/qq-harness-runtime`，安装 Node 24 与固定 runtime：

```sh
npm install --registry=https://registry.npmmirror.com node@24 @deepseek-ai/dsh@0.2.1-rc.2 --no-audit --no-fund
```

将上述官方 SDK 源码目录复制到 `sdk/deepseek_harness`；Python 使用网站 venv（部署时 pydantic 2.13.4）。`dsh` 启动脚本将独立 Node 目录置于 PATH 首位，不修改系统 Node。SDK 的 `DSH_HOME` 指定独立目录。

配置见 `.env.example`。账号服务的 systemd drop-in 与官方机器人 `.env` 均须设置模式；修改后重启账号服务、重新加载官方 gunicorn workers。

回退：两端设置 `QQ_HARNESS_MODE=off` 并重启/重载。保留数据库与人设，不需要卸载 runtime。不要在已部分发送的同一请求中自动回退重放。

## 验证

```sh
python -m unittest qq_bot.test_harness qq_bot.test_agent_turn qq_bot.test_chat_agent qq_bot.test_qq_memes qq_bot.test_qq_service
PYTHONPATH=api_budget python -m unittest discover -s api_budget -p test_budget.py
```

真实测试仅通过已有限定指定好友唯一好友的 `/admin/test-turn`。该端点的 HTTP 200 不是发送证据，必须核对 `private delivery acknowledged`、`private image verification` 与 `qq_harness` turn 日志。禁止用群聊做测试。
