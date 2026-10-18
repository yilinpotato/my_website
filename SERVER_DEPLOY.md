# 🚀 部署文档 — 阿里云 (192.0.2.10 / potatoma.me)

> 本文档描述**当前服务器的真实部署状态**。本机为干净的 Ubuntu 22.04，
> 项目部署在 `/home/myl/my_website`，以普通用户 `myl` 运行（非 root、非宝塔环境）。

## 📋 部署概览

| 服务 | 端口 | 路径 | systemd 单元 | 说明 |
|------|------|------|--------------|------|
| 导航首页 | - | `/` | (nginx 静态) | 静态 HTML |
| New 网站 | 5001 | `/new/` | `potatoma-new` | Flask，AI 运动评估（俯卧撑分析需 torch，当前未装） |
| Old 网站 | 5002 | `/old/` | `potatoma-old` | Flask 经典版 |
| JM 漫画机器人 | 5003 | `/jm/`, `/qq/callback` | `potatoma-jm` | Flask + QQ 机器人 |
| 站点分析管理后台 | 5011 | `/site-admin/` | `potatoma-site-admin` | 独立 Flask/Gunicorn，管理员登录后查看 PostHog |
| Redis | 6379 | - | `redis-server` | New/Old 站任务队列依赖 |
| Nginx | 80 | - | `nginx` | 反向代理 + 静态文件 |

现有服务均已设置**开机自启**；`potatoma-site-admin` 须完成本文的检查、安装与验证步骤后
才视为已上线。

---

## 🗂️ 环境信息

| 项 | 值 |
|----|-----|
| 服务器公网 IP | `192.0.2.10` |
| 域名 | `potatoma.me`（A 记录需指向上面的 IP） |
| 操作系统 | Ubuntu 22.04.5 LTS |
| 系统 Python | `/usr/bin/python3`（3.10.12） |
| 项目虚拟环境 | `/home/myl/my_website/venv` |
| 站点分析后台专用虚拟环境 | `/home/myl/my_website/site_admin/venv` |
| 运行用户 | `myl` |
| 项目路径 | `/home/myl/my_website` |

---

## 1️⃣ 系统依赖（已安装）

```bash
sudo apt-get update
sudo apt-get install -y python3.10-venv nginx redis-server acl
sudo systemctl enable --now redis-server
```

验证 Redis：
```bash
redis-cli ping   # 应返回 PONG
```

---

## 2️⃣ Python 虚拟环境与依赖（已安装）

```bash
cd /home/myl/my_website
python3 -m venv venv

# 核心依赖（使用清华源加速）
./venv/bin/pip install -i https://pypi.tuna.tsinghua.edu.cn/simple \
  Flask Flask-Login Flask-Mail Flask-SQLAlchemy gunicorn redis requests \
  Werkzeug Jinja2 itsdangerous APScheduler tzlocal psutil \
  google-genai websockets numpy pillow opencv-python-headless \
  jmcomic pynacl aiohttp
```

> **torch 未安装**（可选，约 2GB）。未装时 New 站的"俯卧撑分析"功能会返回
> 503 提示不可用，其余功能正常。如需启用：
> ```bash
> ./venv/bin/pip install torch --index-url https://download.pytorch.org/whl/cpu
> sudo systemctl restart potatoma-new
> ```

验证：
```bash
./venv/bin/python -c "import flask, redis, jmcomic, aiohttp; print('All OK')"
```

---

## 3️⃣ Systemd 服务（已配置）

三个服务文件位于 `/etc/systemd/system/`：`potatoma-new.service`、
`potatoma-old.service`、`potatoma-jm.service`。以 New 站为例：

```ini
# /etc/systemd/system/potatoma-new.service
[Unit]
Description=Potatoma New Website (AI运动评估系统)
After=network.target redis-server.service

[Service]
User=myl
Group=myl
WorkingDirectory=/home/myl/my_website/new/myproject
ExecStart=/home/myl/my_website/venv/bin/gunicorn -w 2 -b 127.0.0.1:5001 app:app --timeout 120
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

Old 站同理（端口 5002，目录 `old`）；JM 站为
`gunicorn -w 2 -b 127.0.0.1:5003 jm_bot:app --timeout 300`，目录 `qq_bot`。

启用并启动：
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now potatoma-new potatoma-old potatoma-jm
sudo systemctl is-active potatoma-new potatoma-old potatoma-jm   # 三个都应为 active
```

### 站点分析管理后台

后台使用独立依赖、受保护环境文件和独立 systemd 单元。必须创建后台专用虚拟环境，
不要在 New/Old/JM 共用的 `/home/myl/my_website/venv` 中安装或降级后台依赖：

```bash
cd /home/myl/my_website
python3 -m venv site_admin/venv
site_admin/venv/bin/pip install -r site_admin/requirements.txt
```

创建环境文件并立即限制权限：

```bash
cd /home/myl/my_website
umask 077
cp site_admin/.env.example site_admin/.env
chmod 600 site_admin/.env
openssl rand -hex 32
```

用本机编辑器打开 `site_admin/.env`，填写用户批准的管理员密码，并将
`openssl` 生成的 64 位十六进制值填入 `SITE_ADMIN_SESSION_SECRET`。确认连接信息为：

```dotenv
POSTHOG_PROJECT_ID=576032
POSTHOG_ORGANIZATION_ID=00000000-0000-0000-0000-000000000000
POSTHOG_API_HOST=https://us.posthog.com
```

将用户提供的 PostHog Personal API Key 填入 `POSTHOG_PERSONAL_API_KEY`。只在受保护的
编辑器会话中输入，不要把密钥放入命令行、shell 历史、日志、Git 或部署文档；本文也不
记录管理员密码或 Personal API Key。完成后再次执行并检查权限：

```bash
chmod 600 site_admin/.env
stat -c '%a %U %G %n' site_admin/.env
# 应为：600 myl myl site_admin/.env
```

Nginx 只需要读取后台静态目录。不要放宽 home 或仓库的 Unix mode；给 `www-data` 对三个
父目录仅授予遍历权限，对 `site_admin/static` 才授予读取/遍历权限。默认 ACL 使该目录内
今后新增的静态文件继续可读，但不会传播到同级的 `.env`：

```bash
sudo setfacl -m u:www-data:--x /home/myl
sudo setfacl -m u:www-data:--x /home/myl/my_website
sudo setfacl -m u:www-data:--x /home/myl/my_website/site_admin
sudo setfacl -R -m u:www-data:rX /home/myl/my_website/site_admin/static
sudo setfacl -m d:u:www-data:rX /home/myl/my_website/site_admin/static
sudo setfacl -x u:www-data /home/myl/my_website/site_admin/.env 2>/dev/null || true
chmod 600 site_admin/.env
sudo -u www-data test -r /home/myl/my_website/site_admin/static/admin.css
sudo -u www-data test ! -r /home/myl/my_website/site_admin/.env
```

最后两条命令必须同时成功：静态 CSS 可读，受保护环境文件不可读。不得对 `/home/myl`、
仓库或整个 `site_admin` 使用面向 other 的读/执行权限。

创建运行时配置或执行任何线上操作前，先运行完整的离线预部署套件：

```bash
cd /home/myl/my_website
node --test tests/*.test.js
node --test site_admin/tests/admin_js.test.js
/home/myl/my_website/site_admin/venv/bin/python -m unittest discover -s site_admin/tests -p 'test_*.py' -v
(cd qq_bot && /home/myl/my_website/venv/bin/python -m unittest -v test_jm_auth.py)
/home/myl/my_website/site_admin/venv/bin/python -m py_compile site_admin/*.py
/home/myl/my_website/venv/bin/python -m py_compile qq_bot/jm_bot.py
for file in home-controls.js project-home.js site_admin/static/admin.js new/myproject/static/training.js; do node --check "$file"; done
git diff --check
```

先执行只读检查。它默认等同于 `--check`，只显示项目 ID 和七个字段的安全差异，不修改
PostHog：

```bash
/home/myl/my_website/site_admin/venv/bin/python site_admin/configure_posthog.py --check
```

核对差异后，才显式应用已批准的设置。脚本 PATCH 后会重新 GET 并逐项验证七个字段；
再次执行 `--apply` 时若已一致，不会发送 PATCH：

```bash
/home/myl/my_website/site_admin/venv/bin/python site_admin/configure_posthog.py --apply
/home/myl/my_website/site_admin/venv/bin/python site_admin/configure_posthog.py --check
```

这七个字段会将时区设为 `Asia/Shanghai`、保留原始 IP、关闭会话回放/控制台采集/性能
采集、将 PostHog 云端事件保留期设为 12 个月；后台界面和导出始终只查询最近 90 天，并将项目
网站 URL 精确设为 `https://potatoma.me`。
`app_urls` 用于网站分析和 toolbar 的授权网站 URL；它不是对公开采集 token 的保密保护。

提交给 systemd 前先做离线验证，然后安装并启动独立单元：

```bash
cd /home/myl/my_website
systemd-analyze verify site_admin/deploy/potatoma-site-admin.service
sudo install -o root -g root -m 0644 \
  site_admin/deploy/potatoma-site-admin.service \
  /etc/systemd/system/potatoma-site-admin.service
sudo systemctl daemon-reload
sudo systemctl enable --now potatoma-site-admin
sudo systemctl is-enabled potatoma-site-admin
sudo systemctl is-active potatoma-site-admin
sudo systemctl show potatoma-site-admin \
  -p User -p Group -p MainPID -p ExecMainStatus -p FragmentPath
ss -tlnp | grep ':5011'
curl -sS -D - -o /dev/null http://127.0.0.1:5011/
```

单元固定运行一个 Gunicorn worker，以保持进程内登录限速器的唯一权威；CSV 有 25 秒内部
截止时间，Gunicorn 与 Nginx 均保留 45 秒外层预算。`ProtectSystem=strict` 和
`ProtectHome=read-only` 令代码及
环境文件只读；`PrivateTmp=true` 保留应用所需的私有临时目录；
`RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6` 保留 Gunicorn socket 与 PostHog HTTPS
网络访问。

---

## 4️⃣ Nginx 反向代理 + HTTPS（已配置）

配置文件：`/etc/nginx/sites-available/potatoma.conf`（已软链到 `sites-enabled/`，
默认站点 `default` 已移除）。公共 location 片段抽到
`/home/myl/my_website/nginx_site_common.conf`。三个 server 块的职责严格分离：

1. **potatoma.me / www（443 SSL）** — Let's Encrypt 证书，提供完整站点
2. **potatoma.me / www（80）** — 301 跳转 HTTPS（ACME challenge 路径除外）
3. **mayilin.xyz / www + 直接 IP（80, default_server）** — HTTP 提供站点

TLS server 与默认 HTTP server 各 include 一次 `nginx_site_common.conf`；
`potatoma.me` 的 HTTP server 不 include 公共片段，只保留单独的 ACME location 和
其余请求的 HTTPS 跳转。公共片段除 new/old/jm 反代外，还有
**MyGO 静态收录站**（纯静态，来源：窜子）和 **MyAgents Lite**（纯静态、本地保存）：
```nginx
location /mygo/ {
    alias /home/myl/my_website/mygo-site/;
    index index.html;
    try_files $uri $uri/ /mygo/index.html;
}
```
mygo-site 全部用相对路径引用资源，无需后端，nginx alias 直接提供即可。

```nginx
location /myagents/ {
    alias /home/myl/my_website/myagents/lite/;
    index index.html;
    try_files $uri $uri/ /myagents/index.html;
}
```

> 不要在这台 3.4 GiB 内存服务器上启动 `myagents` 上游完整版本或执行其前端构建。
> 完整版依赖 Java、MySQL、Redis、Milvus、Ollama 和 Mem0；本站只部署零常驻内存的 Lite 页面。

公共片段关键点（首页用 `try_files` 避免 `index` 触发内部重定向落到 nginx 默认页）：
```nginx
location ^~ /.well-known/acme-challenge/ { root /home/myl/my_website; try_files $uri =404; }
root /home/myl/my_website;
location = / { try_files /index.html =404; }
location /new/static/ { alias /home/myl/my_website/new/myproject/static/; expires 7d; }
location /new/ { proxy_pass http://127.0.0.1:5001/; proxy_set_header X-Forwarded-Prefix /new; ... }
location /old/static/ { alias /home/myl/my_website/old/static/; expires 7d; }
location /old/ { proxy_pass http://127.0.0.1:5002/; proxy_set_header X-Forwarded-Prefix /old; ... }
location /jm/ { proxy_pass http://127.0.0.1:5003/; ... }
location /qq/callback { proxy_pass http://127.0.0.1:5003/qq/callback; ... }
```

公共 `nginx_site_common.conf` 不得出现任何 `/site-admin` location。将专用片段安装到
Nginx snippets 目录：

```bash
cd /home/myl/my_website
sudo install -o root -g root -m 0644 nginx_site_admin_https.conf \
  /etc/nginx/snippets/potatoma-site-admin-https.conf
```

编辑 `/etc/nginx/sites-available/potatoma.conf`：专用片段只能 include 在
`potatoma.me` 的 TLS server 中。默认/其他域名的 HTTP server 只 include 公共片段；
`potatoma.me` 的 HTTP server 不得 include 公共或后台片段，以免在明文端口重复提供站点，
而是显式保留一个 ACME location，并将其余所有请求跳转到 HTTPS。

<!-- site-admin-nginx-context:start -->
```nginx
server {
    listen 443 ssl;
    server_name potatoma.me www.potatoma.me;
    ssl_certificate /etc/letsencrypt/live/potatoma.me/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/potatoma.me/privkey.pem;
    # 保留现有其他 TLS server 级配置。
    include /home/myl/my_website/nginx_site_common.conf;
    include /etc/nginx/snippets/potatoma-site-admin-https.conf;
}

server {
    listen 80;
    server_name potatoma.me www.potatoma.me;

    location ^~ /.well-known/acme-challenge/ {
        root /home/myl/my_website;
        default_type "text/plain";
        try_files $uri =404;
    }

    location / {
        return 301 https://$host$request_uri;
    }
}

server {
    listen 80 default_server;
    server_name mayilin.xyz www.mayilin.xyz _;
    include /home/myl/my_website/nginx_site_common.conf;
}
```
<!-- site-admin-nginx-context:end -->

专用片段内 `/site-admin/static/` 写在代理之前，避免静态资源进入 Gunicorn。代理的
`location` 与 `proxy_pass` 都以 `/` 结尾，因此 Nginx 会剥离内部请求的
`/site-admin/` 前缀，并将不带末尾斜线的 `/site-admin` 规范化重定向为
`/site-admin/`。固定的 `X-Forwarded-Prefix /site-admin` 让应用生成外部 URL；
`proxy_redirect off` 避免 Nginx 再次改写应用已经带正确前缀的重定向。后台代理的
`proxy_read_timeout` 与 `proxy_send_timeout` 均固定为 45 秒，与 Gunicorn 外层预算一致。

不得删除或替换公共片段中的全局 `sub_filter` 分析注入（公开 token 可出现在浏览器端），
也不得修改现有 `/`、`/new/`、`/old/`、`/jm/`、`/qq/callback`、`/mygo/` 或
`/myagents/` 路由。上线前必须先验证完整配置，再重载：

```bash
sudo nginx -t
sudo nginx -T 2>/dev/null | grep -n -E \
  'listen 443 ssl|listen 80|potatoma-site-admin-https.conf|location /site-admin/'
sudo systemctl reload nginx
```

### HTTPS（Let's Encrypt）

已为 **potatoma.me / www.potatoma.me** 签发证书（certbot，`certbot.timer` 自动续期）：
```bash
sudo certbot certificates          # 查看证书
sudo certbot renew --dry-run       # 测试续期
```

> **mayilin.xyz 暂未签发 HTTPS。** ACME 验证时 Let's Encrypt 从境外访问
> mayilin.xyz 返回 403（本机及国内访问均正常 200），疑似该域名在阿里云侧开启了
> CDN / WAF / 境外访问拦截。需在阿里云控制台关闭对应拦截后再执行：
> ```bash
> sudo certbot --nginx -d mayilin.xyz -d www.mayilin.xyz \
>   --register-unsafely-without-email --agree-tos --redirect --non-interactive
> ```
> 成功后把 mayilin.xyz/www 从第 3 个 HTTP server 块挪到 443 SSL 块。

---

## 5️⃣ 防火墙 / 安全组

- 系统层：ufw 未启用，iptables INPUT 默认 ACCEPT（无限制）。
- 云层：需在**阿里云控制台 → ECS → 安全组 → 入方向**放行 TCP `80` 和 `443`。
  **当前 80、443 均已放行。**

---

## 6️⃣ QQ 机器人配置

编辑 `qq_bot/jm_bot.py`：
```python
QQ_APP_ID = "102828452"
QQ_APP_SECRET = "..."        # 已填
SERVER_DOMAIN = "https://potatoma.me/jm"
```
在 [QQ 开放平台](https://q.qq.com/) 将回调地址设为 `https://potatoma.me/qq/callback`
（需先完成 DNS 指向本机 + HTTPS）。

---

## ✅ 验证部署

```bash
# 服务器本地（任何时候都应通过）
curl -I http://127.0.0.1/            # 导航首页
curl -I http://127.0.0.1/new/        # New
curl -I http://127.0.0.1/old/        # Old
curl -I http://127.0.0.1/jm/         # JM
curl -I -H 'Host: mayilin.xyz' http://127.0.0.1/site-admin/  # 应为 404
curl -I http://192.0.2.10/site-admin/                    # 应为 404
curl -I http://potatoma.me/site-admin/                      # 仅重定向到 HTTPS
curl -I https://potatoma.me/site-admin                      # 规范化到 /site-admin/
curl -I https://potatoma.me/site-admin/                     # 未登录时跳转到登录页
curl -I https://potatoma.me/site-admin/static/admin.css     # 应为 200

# 公网（安全组放行 80 后）
curl -I http://192.0.2.10/
```

| 页面 | 地址 |
|------|------|
| 导航首页 | http://192.0.2.10/ 或 http://potatoma.me/ |
| New 网站 | …/new/ |
| Old 网站 | …/old/ |
| JM 漫画 | …/jm/ |
| 站点分析管理后台 | https://potatoma.me/site-admin/ |

后台上线后还要完成以下验证：

```bash
cd /home/myl/my_website
/home/myl/my_website/site_admin/venv/bin/python site_admin/configure_posthog.py --check  # 应无字段差异
curl -fsS https://potatoma.me/ | grep '__SITE_ANALYTICS_CONFIG__'
curl -sS -D - -o /dev/null https://potatoma.me/site-admin/
curl -fsS -o /dev/null https://potatoma.me/site-admin/static/admin.css
sudo -u www-data test -r /home/myl/my_website/site_admin/static/admin.css
sudo -u www-data test ! -r /home/myl/my_website/site_admin/.env
sudo journalctl -u potatoma-site-admin -n 50 --no-pager
```

在浏览器访问 `https://potatoma.me/site-admin/`，验证错误密码被拒绝、批准的管理员密码可
登录、仪表盘/筛选/CSV 导出正常、登出后受保护页面重新要求登录，并确认响应包含
`Cache-Control: no-store`。日志和页面中都不应出现 Personal API Key、管理员密码、
Authorization header 或 PostHog 原始错误正文。

### 站点分析管理后台回滚

只回滚后台，不影响现有站点和全局分析注入：

```bash
sudo systemctl disable --now potatoma-site-admin
```

从 `potatoma.me` TLS server 仅删除以下一行，保留公共 include、`sub_filter` 公开分析
token 和全部现有路由：

```nginx
include /etc/nginx/snippets/potatoma-site-admin-https.conf;
```

删除仅该已安装片段，验证并重载：

```bash
sudo rm /etc/nginx/snippets/potatoma-site-admin-https.conf
sudo nginx -t
sudo systemctl reload nginx
```

撤销仅为后台静态目录添加的 `www-data` ACL，不改变 Unix mode，也不影响仓库其他目录。
先从 `static` 下每个目录递归删除继承的 default ACL，再递归删除文件/目录的 access ACL，
最后按从深到浅的顺序删除父目录遍历 ACL。每条命令都容忍条目已不存在，因此整段可重复执行：

```bash
sudo find /home/myl/my_website/site_admin/static -type d -exec setfacl -x d:u:www-data {} + 2>/dev/null || true
sudo setfacl -R -x u:www-data /home/myl/my_website/site_admin/static 2>/dev/null || true
sudo setfacl -x u:www-data /home/myl/my_website/site_admin 2>/dev/null || true
sudo setfacl -x u:www-data /home/myl/my_website 2>/dev/null || true
sudo setfacl -x u:www-data /home/myl 2>/dev/null || true
```

如需移除已安装单元，可再删除仅该单元并刷新 systemd；保留 `site_admin/.env` 以便恢复，
或通过受控密钥流程单独轮换/销毁，切勿打印其内容：

```bash
sudo rm /etc/systemd/system/potatoma-site-admin.service
sudo systemctl daemon-reload
```

---

## 🔧 常用命令

```bash
# 服务状态 / 重启
sudo systemctl status  potatoma-new potatoma-old potatoma-jm --no-pager
sudo systemctl status potatoma-site-admin --no-pager
sudo systemctl restart potatoma-new potatoma-old potatoma-jm potatoma-site-admin

# 日志
sudo journalctl -u potatoma-new -f
sudo journalctl -u potatoma-jm  -f
sudo journalctl -u potatoma-site-admin -f

# 重载 nginx
sudo nginx -t && sudo systemctl reload nginx

# 检查端口监听
ss -tlnp | grep -E ':(80|500[123]|5011|6379)'
```

### 更新代码后重新部署
```bash
cd /home/myl/my_website
git pull origin main
# 后台如有新依赖：site_admin/venv/bin/pip install -r site_admin/requirements.txt
sudo systemctl restart potatoma-new potatoma-old potatoma-jm
sudo systemctl restart potatoma-site-admin
```

---

## 🔐 （可选）配置 HTTPS

DNS 指向本机后：
```bash
sudo apt-get install -y certbot python3-certbot-nginx
sudo certbot --nginx -d potatoma.me -d www.potatoma.me
```
certbot 会自动改写 nginx 配置并配置证书自动续期。

---

## ❓ 常见问题

**502 Bad Gateway** — 对应后端服务挂了：
```bash
sudo systemctl status potatoma-new
sudo journalctl -u potatoma-new -n 50 --no-pager
```

**CSS / 静态文件 404** — 确认 TLS server include 了后台专用片段，并重新执行本文的
`www-data` 窄范围 ACL 与 `sudo -u www-data test -r` 验证；不要放宽 home/仓库的 other 权限。

**域名打不开但 IP 能开** — DNS 未指向本机或未生效：
```bash
nslookup potatoma.me 223.5.5.5    # 应返回 192.0.2.10
```

**俯卧撑分析返回 503** — 未装 torch，见第 2 节。
