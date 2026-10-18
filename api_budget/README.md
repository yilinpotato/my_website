# 全站 DeepSeek 额度

服务器共享模块：`/home/myl/api-budget/site_budget.py`，SQLite 账本：同目录 `usage.sqlite3`（非公开目录）。两个虚拟环境通过 `.pth` 加载同一模块。

- 全站每天 3 元，每个访问 IP 每天 0.5 元，按北京时间请求发起日期计账。
- 当前覆盖学习助手所有文本生成，以及 MyAgents 的规划、每个员工调用和汇总。
- 运动评估未配置 Gemini/DeepSeek 密钥；其他在线项目未发现付费模型调用。新接入的模型调用必须经过 `completion()`，不要直接调用供应商。
- 使用官方 `deepseek-flash`，关闭思考和流式输出，输出最多 4096 token。
- 按 2026-09-25 官方高峰单价保守记账：缓存输入 0.04 元/M、未缓存输入 2 元/M、输出 8 元/M。实际低峰费用可能更低。价格变更时需更新模块中的价格常量。
- SQLite `BEGIN IMMEDIATE` 原子预留预算，输入按 UTF-8 字节数加消息封装余量估计上界；返回 usage 后结算。超时、无 usage 或进程退出保留预留额度，不自动释放；明确未受理的 4xx 释放。每天 23:57 后暂停新请求，零点恢复。
- 信任的 IP 来自仅监听 loopback 的服务前面的 nginx `X-Real-IP` 覆写。ProxyFix 的原始 peer 也会检查。公共代理/NAT 用户共享额度，换网络可能获得另一份个人额度，但全站总额仍共用。
- 超限为 HTTP 429，JSON `code=daily_budget_exceeded`，兼容旧接口捕获 RuntimeError 的行为。
- 查询 `/ai/api/budget` 或 `/myagents/api/budget` 可查看当天总量和自己的额度占用（含预留）。账本从部署时开始，不能追溯部署之前的 token 费用。
- 测试：在安装 Flask/requests 的 Python 环境中执行 `python api_budget/test_budget.py`。

官方价格依据：https://api-docs.deepseek.com/zh-cn/quick_start/pricing/
