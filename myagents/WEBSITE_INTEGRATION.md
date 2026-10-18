# MyAgents Lite 子应用

主站的 `/myagents/` 使用 MyAgents Lite Plus：DeepSeek API 负责真实对话，SQLite 保存员工、任务、消息和长期记忆，SQLite FTS5 进行知识库全文检索。没有本地模型、Java、Docker、MySQL、Milvus、Ollama、Mem0 或 Redis 依赖。

页面文件位于 `lite/`，后端监听 `127.0.0.1:5010`，由 Nginx 将 `/myagents/api/` 反向代理过去。复制 `.env.example` 为 `.env` 后设置 `DEEPSEEK_API_KEY`，再安装并启用 `myagents-lite.service`。服务限制为 `MemoryMax=350M`。

原始仓库 `cool-admin-java/`、`cool-admin-vue/` 与 `python-services/` 仅作为上游参考源码，**不要在这台 3.4 GiB 内存的服务器上启动或构建它们**。
