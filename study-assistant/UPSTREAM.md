<div align="center">

# AI Study Assistant

智能学习与个性化辅导平台 · Project 1

</div>

## 🎯 项目主题

- **目标用户**：初高中学生以及家长/教师支持群体。
- **核心痛点**：课堂难理解、课后难复盘、自律性不足。
- **解决方案**：提供“课中记录 + 课后复习 + 激励反馈”一体化 AI 学习伴侣。

## 🧩 模块概览

| 模块 | 说明 | AI/技术栈 |
| --- | --- | --- |
| Note Assistant | 课堂录音上传、自动转写 + 结构化关键点总结 | Whisper/Faster-Whisper · GPT 综述 |
| Mind Map View | 将知识点层级转为交互式心智图，辅助回顾 | Mermaid.js / MindElixir.js |
| Error Book Manager | 拍照/上传错题 → OCR 识别 → 分类解析与举一反三练习 | PaddleOCR / 云 OCR · LLM 题目分析 |
| Learning Dashboard | 展示学习时长、掌握度、复习进度与提醒 | Chart.js / ECharts · 日志聚合 |
| Parent View | 面向家长的学习周报、弱项提示与鼓励文案 | GPT 风格摘要 |

## 💡 创新亮点

- **智能题目诊断链（Question Insight Engine）**：
	1. OCR/LaTeX 解析题干与作答步骤，生成结构化树；
	2. LLM + 规则混合比对“标准解 → 学生解”，判断是否正确；
	3. 通过轻量分类模型（如 XGBoost / DistilBERT）输出错因标签（概念未掌握、公式套错、计算粗心、表达不清等），并自动写出“为什么错 + 如何改”。
- **错因证据链 & 可信度得分**：为每条判定附带引用（题干片段、学生答案行号、标准推导步骤），以 `score + rationale` 形式展示，家长和老师能快速核验 AI 判断。
- **知识薄弱图谱与预测复习**：将错因标签映射到课程知识图（章节 → 知识点 → 题型），计算“薄弱度”并预测下次复习窗口，驱动 Mind Map 自动高亮薄弱节点、Dashboard 推送复习任务。
- **自适应练习生成器**：结合错因与薄弱图谱，向向量库检索相似题，再由 LLM 对题干做“难度微调 / 场景重写”，逐步提升挑战；可把学生的思路再送入“同伴点评”模型，生成鼓励与建议。
- **学习情绪 & 动机陪伴**：在家长/学生端接入情绪温度计（基于交互文本 + 学习节奏），触发不同风格的鼓励语、连续打卡奖励与 AI 学习教练问候语，缓解“缺乏自驱”的痛点。

## 🗄️ 数据存储（MySQL 规范）

- **库命名**：`ai_study_assistant`，统一使用 UTF8MB4，时区设为 `+08:00`。
- **主表**：
	- `study_logs`（学习仪表盘数据）
		```sql
		CREATE TABLE study_logs (
			id BIGINT PRIMARY KEY AUTO_INCREMENT,
			student_id BIGINT NOT NULL,
			subject VARCHAR(32) NOT NULL,
			focus_minutes INT NOT NULL,
			mastery_score TINYINT NOT NULL,
			log_date DATE NOT NULL,
			UNIQUE KEY uniq_student_subject_day (student_id, subject, log_date)
		);
		```
	- `review_queue`（复习排程）
		```sql
		CREATE TABLE review_queue (
			id BIGINT PRIMARY KEY AUTO_INCREMENT,
			student_id BIGINT NOT NULL,
			title VARCHAR(120) NOT NULL,
			priority ENUM('高','中','低') DEFAULT '中',
			next_review_at DATETIME NOT NULL,
			source ENUM('error_book','note','practice') DEFAULT 'error_book'
		);
		```
	- `parent_reports`（家长视图周报）
		```sql
		CREATE TABLE parent_reports (
			id BIGINT PRIMARY KEY AUTO_INCREMENT,
			student_id BIGINT NOT NULL,
			week_start DATE NOT NULL,
			week_end DATE NOT NULL,
			ai_summary TEXT,
			encouragement TEXT,
			weak_topics JSON,
			UNIQUE KEY uniq_student_week (student_id, week_start, week_end)
		);
		```
- **接入约定**：
	- 前端 Dashboard 通过 `/api/dashboard/overview` 读取 `study_logs` 聚合数据，`/api/dashboard/review-queue` 映射 `review_queue`。
	- Parent View 通过 `/api/parents/report?student_id=xxx` 读取最新 `parent_reports`，若缺失则触发 LLM 生成并写回。
	- 所有时间字段统一存储 UTC，入库/出库时转换为本地时间，并在列注释中说明单位（分钟/百分比）。

## 👤 用户认证与邮箱验证

- **功能**：
	- 顶栏入口新增“学习仪表盘 / 家长视图 / 错题本 / 登录 / 注册 / 个人中心”。
	- 注册 → 发送 6 位验证码（默认使用 `smtp.163.com`，账号由环境变量配置）。
	- 邮箱验证成功后自动生成随机昵称，可在 `Profile` 页面修改昵称、年龄、选课、身份、绑定家长/学生，并可更改密码。
	- 支持忘记密码邮件、家长与学生互绑查看报告。
- **后端环境变量**：
	- 可参考 `ai-assistant-backend/.env.example`，本地复制为 `.env` 后再填写（不要提交到 Git）。
	```bash
	# 使用 PyMySQL 直连 MySQL
	DATABASE_URL="mysql+pymysql://user:pass@host:3306/ai_study_assistant"
	MAIL_USERNAME="your_email@example.com"
	MAIL_PASSWORD="your_smtp_app_password"
	MAIL_SERVER="smtp.163.com"
	MAIL_PORT=465
	MAIL_USE_SSL=True
	MAIL_DEFAULT_SENDER="AI Study Assistant <your_email@example.com>"
	```
- **核心接口**：
	- `POST /api/auth/register`：注册（email、username、password、role、display_name）。
	- `POST /api/auth/verify`：邮箱 + 验证码，返回 token。
	- `POST /api/auth/login`：邮箱密码登录，返回 token。
	- `POST /api/auth/request-password-reset` / `POST /api/auth/reset-password`：邮箱验证码重置密码。
	- `GET /api/profile`：携带 `Authorization: Bearer <token>` 获取个人资料。
	- `PUT /api/profile`：更新昵称/年龄/课程/角色/绑定邮箱。
	- `PUT /api/profile/password`：修改密码。
- **前端配置**：可从 `ai-assistant-frontend/.env.example` 复制为 `.env`，设置 `VITE_API_BASE_URL=http://localhost:3000`（或部署地址）。Pinia `auth` 仓库会自动附带 token。若前端显示 `Failed to fetch`，通常表示后端 `python app.py` 未启动或端口不匹配，请先确认 API 可访问。

## 🏗️ 技术框架

- **前端**：Vue 3 + Vite + Pinia + Vue Router，配合 `base.css`/`main.css` 自定义柔和配色与玻璃拟态风格。
- **后端**：Flask (Blueprints) + Flask-CORS，后续可扩展 Celery+Redis 运行异步 AI 任务。
- **AI 能力**：语音转写 (Whisper/Deepgram)、OCR (PaddleOCR/百度 OCR)、LLM 总结/问答 (OpenAI、通义、百川等)。
- **数据存储**：PostgreSQL/MySQL 保存结构化数据，错题与讲义可结合 pgvector/Milvus 建相似度检索。

```
computer software/
├─ README.md                 # 项目说明（本文件）
├─ ai-assistant-backend/     # Flask API (语音转写/错题管理等)
└─ ai-assistant-frontend/    # Vue 3 Web App (学生、家长视图)
```

## 🚀 快速开始

```powershell
# 启动后端
cd "C:\Users\qqrtq\Documents\GitHub\computer-software\ai-assistant-backend"
conda activate ai-backend
python app.py

# 启动前端
cd "C:\Users\qqrtq\Documents\GitHub\computer-software\ai-assistant-frontend"
npm install
npm run dev
```

> 默认后端监听 `http://localhost:3000`，前端 Vite Dev Server 运行在 `5173` 端口。根据需要在 `.env` 中配置 API 地址与 AI Key。

## 🌐 本地查看网页 & 测试结果

1. **确保服务已启动**：后端 `python app.py`（或 `flask run`）保持运行，前端目录执行 `npm run dev -- --host 0.0.0.0 --port 5173 --strictPort`。
2. **打开浏览器**：访问 `http://localhost:5173`，即可实时查看首页（Hero/模块目录/学习旅程）。修改 `src/` 下文件会自动热更新，方便调试当前“测试结果”。
3. **查看构建产出**：若需验证发布前效果，可以运行：

```powershell
cd "ai-assistant-frontend"
npm run build
npm run preview -- --host 0.0.0.0 --port 4173 --strictPort
```

随后访问 `http://localhost:4173`，即可体验与线上一致的静态构建版本。按 `Ctrl + C` 可退出对应服务。

## ☁️ 同步到 GitHub

1. **初始化或检查 Git 仓库**：在项目根目录（`computer software/`）运行 `git status`，确认 `.gitignore` 已过滤 `.venv/`、`node_modules/`、`dist/` 等文件。若尚未初始化，可执行 `git init`。
2. **配置提交信息**（首次使用 Git 可设置用户名/邮箱）：

```powershell
git config user.name "Your Name"
git config user.email "you@example.com"
```

3. **查看变更并提交**：

```powershell
git status
git add .
git commit -m "feat: initial AI study assistant"
```

4. **绑定远端仓库**：

```powershell
git remote add origin https://github.com/<your-account>/<repo-name>.git
git branch -M main
```

如已绑定，可用 `git remote -v` 查看。

5. **推送到 GitHub**：

```powershell
git push -u origin main
```

后续只需重复 “修改 → `git add` → `git commit -m "..."` → `git push`” 流程即可同步更新。需要换电脑或拉取团队修改时，在根目录执行 `git pull origin main`。

## 📅 迭代路线

1. **MVP**：完成 Note Assistant（音频上传→转写→摘要），Dashboard 显示基础统计。
2. **错题管理**：OCR + LLM 解析、错题分类与推送相似练习题。
3. **心智图 & 家长视图**：可视化知识树、周报生成以及激励机制（学习打卡、复习提醒）。
4. **多模态增强**：引入多模态 LLM、语音对话辅导、PWA/移动端适配。

## 🤝 贡献与后续

- 建议为前后端分别添加单元测试/端到端测试（Vitest、Pytest）。
> **提示**：完成一次安装后，今后每次重新打开终端只需 `cd "ai-assistant-backend"` 并执行 `venv\Scripts\activate` 进入虚拟环境，再运行 `python app.py` 或其他脚本。使用 `deactivate` 可退出虚拟环境。
- 可通过 Docker Compose 编排 Flask + Redis + 数据库 + 前端 Nginx 容器。
- 欢迎继续完善 API 文档、设计稿、数据标注流程或接入学校管理系统。

> 有任何想法/问题，直接在 README 中记录或开启 issue，方便团队协作。
