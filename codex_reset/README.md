# Codex 重置邮件提醒

页面：`https://potatoma.me/codex-reset/`。

独立 Flask 服务，使用 LimitReset 免费公开 JSON API：

- `https://limitreset.net/api/v1/codex/latest`：检查 `updatedAt`，超过两小时停止生成提醒。
- `https://limitreset.net/api/v1/codex/events`：订阅 `upcoming`、`reset`、`banked` 三类事件。
- 来源文档：<https://limitreset.net/api-docs>。数据为第三方记录，保留原始公告及 CC BY 4.0 归属。

预告不当作已完成；预测概率不触发邮件。首次同步只建立基线，确认订阅前发布的记录不补发；停机恢复最多补发最近 48 小时内的新记录。按原帖 URL + 事件类型去重，类型由预告变为重置时可再次通知。

邮箱验证链接有效 24 小时。打开链接只显示确认页，点击确认才订阅，避免邮件扫描器自动激活。退订链接可重复使用，支持 RFC 8058 POST 一键退订；普通 GET 不退订。修改偏好通过退订后重新验证完成。

邮件进入 SQLite 队列，由分钟任务发送。失败指数退避重试，最多六次；相同消息使用稳定 Message-ID。SMTP 在接收后断线等不确定结果下，不能保证严格 exactly-once，极少数情况下可能重复。已发送邮件内容从队列清除。未验证及退订邮箱仍保留在服务器，便于限制滥发和处理退订链接；页面不公开订阅者数据。

## 部署与管理

源文件位于仓库 `codex_reset/`，部署副本在 `/opt/codex-reset`，专用用户 `codex-reset`；SQLite 在 `/var/lib/codex-reset/state.sqlite3`。配置 `/etc/codex-reset/config.json` 权限 0640、root:codex-reset，凭据不入库。服务使用服务器现有 Python 虚拟环境。

配置结构（替换占位符，不提交真实配置）：

```json
{
  "database": "/var/lib/codex-reset/state.sqlite3",
  "secret": "random-secret",
  "base_url": "https://potatoma.me/codex-reset",
  "smtp": {"host": "smtp.163.com", "port": 465, "ssl": true,
           "username": "sender@example.com", "password": "smtp-auth-code",
           "sender": "sender@example.com"}
}
```

```sh
systemctl status codex-reset-web codex-reset-poll.timer
journalctl -u codex-reset-poll --since today
systemctl start codex-reset-poll
systemctl stop codex-reset-poll.timer
```

公网 `/codex-reset/health` 返回不含隐私的同步健康状态。邮件 `failed` 记录只保存异常类型，不保存 SMTP 凭据。排查失败队列应在服务器上读取 SQLite，不向网页暴露邮箱。

Nginx 只转发 HTTPS `/codex-reset/` 到 `127.0.0.1:5021/`，覆盖客户端转发头。禁用此路径 access log，避免日志记录邮箱验证及退订令牌。应用本身禁止缓存、嵌套 iframe 和外部脚本。

## 验证

```sh
cd codex_reset
python -m unittest -v test_reset
```

测试使用临时数据库和模拟发信接口，不给真实用户发送邮件。SMTP 登录成功仅证明凭据有效；最终邮箱收件需要在页面填写自己的邮箱完成验证。
