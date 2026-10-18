# 学习助手的站点部署

来源：<https://github.com/yilinpotato/computer-software>，基线提交 `5afec3472a7ba5105a576f1eebc0560deef95a45`。

本目录保留 Vue 前端和 Flask 后端源码。部署地址为 `/ai/`，API 为 `/ai/api/`。

## 当前适配

- 独立访客账号，令牌由浏览器保存；登录、笔记与错题接口沿用原项目的数据权限。
- 使用服务器的 `DEEPSEEK_API_KEY` 调用 DeepSeek，密钥不进入前端。
- CPU 使用 faster-whisper base int8 转写和 RapidOCR 图片识别，降低内存占用。
- 无邮件配置时关闭邮件注册入口，提供访客体验和示例课堂。
- 私有源码、数据库和模型都位于网站静态根目录之外。

## 构建

在 `frontend` 下执行 `npm ci`，设置 `VITE_API_BASE_URL=/ai`，然后 `npm run build`。
将 `dist/assets` 先发布至服务器 `/home/myl/my_website/ai/assets`，最后替换 `dist/index.html`。

后端安装 `backend/requirements-production.txt`；入口为 `wsgi:app`，单 worker、双线程，绑定 `127.0.0.1:5012`。

服务器目录：

- `/home/myl/apps/study-assistant/backend`：后端源码。
- `/home/myl/apps/study-assistant/venv`：独立 Python 环境。
- `/home/myl/apps/study-assistant/models/whisper-base`：转写模型。
- `/home/myl/.local/share/study-assistant`：SQLite 和上传目录。
- `/home/myl/apps/study-assistant/production.env`：部署配置。
- `/etc/systemd/system/potatoma-study.service`：服务定义。

`systemctl restart potatoma-study` 重启服务，`journalctl -u potatoma-study` 查看日志。
Nginx 定向配置位于 `/home/myl/my_website/nginx_site_common.conf`；修改前备份，运行 `nginx -t` 后再 reload。
健康检查 `/ai/api/health` 只检查服务进程，不代表付费模型额度可用。摘要、题目解析等仍需要有效 API 余额。

原项目说明见 `UPSTREAM.md`，其模型和数据库描述不完全等同于本部署。
