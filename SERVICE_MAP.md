# 服务源码目录

本仓库保存可公开的服务代码、前端资源、测试和配置示例。源码从线上部署版本整理，并补充本地前端源码与游戏页面。

| 目录 | 用途 |
| --- | --- |
| `new/myproject` | 运动分析与新版个人工具网站 |
| `old` | 经典工具站与文件库 |
| `qq_bot` | QQ 机器人与漫画网页服务 |
| `study-assistant` | 学习助手前后端 |
| `myagents/lite` | 智能员工轻量服务 |
| `site_admin` | 站点统计后台，凭据从环境变量读取 |
| `api_budget` | 共享模型预算与用量控制 |
| `codex_reset` | 公开订阅及重置提醒服务 |
| `mygo-site`、`game`、`about` | 静态页面及本地游戏源码 |

这里不包含真实环境变量、API/SMTP 密钥、用户数据库、上传文件、访问日志、私人邮件收件配置或运维备份。个人简介中的公开联系方式和第三方许可证署名保留。

## 腾讯云部署

主站为 [mayilin.xyz](https://mayilin.xyz/)，`www.mayilin.xyz` 也提供 HTTPS。腾讯云使用 [nginx_mayilin.conf](nginx_mayilin.conf) 加载 [公共路由](nginx_site_common.conf)、[统计后台路由](nginx_site_admin_https.conf) 和 [订阅服务路由](codex_reset/nginx.conf)。后两个文件在服务器上的名称分别为 `/etc/nginx/snippets/potatoma-site-admin-https.conf` 和 `/etc/nginx/snippets/potatoma-codex-reset.conf`。

QQ 账号机器人和官方机器人共用 `QQ_SERVER_DOMAIN=https://mayilin.xyz/jm`。阅读链接格式为 `/jm/view/<漫画号>`。阅读密码和会话签名密钥由服务器环境提供。证书使用 Certbot 的 webroot 方式签发，验证目录为 `/home/myl/my_website`，续期后重新加载 Nginx。

学习助手的后端部署依赖见 `study-assistant/backend/requirements-production.txt`，前端源码位于 `study-assistant/frontend`。
