# 网站部署指南

本指南帮助你在一个域名下部署多个网站项目。

## 📁 项目结构

```
my_website/
├── index.html          # 导航根页面
├── nginx.conf.example  # Nginx 配置示例
├── start_all.py        # 一键启动所有服务
├── new/myproject/      # AI运动评估系统 (新版)
├── old/                # 经典版网站
└── qq_bot/             # JM漫画查看器 + QQ机器人
    └── jm_bot.py
```

## 🖥️ 本地测试

一键启动所有服务：
```bash
python start_all.py
```

或分别启动：
```bash
# 导航页面 (端口 8000)
python -m http.server 8000

# 新版网站 (端口 5001)
cd new/myproject && python run_local.py

# 旧版网站 (端口 5002)
cd old && python run_local.py

# JM漫画查看器 (端口 5003)
cd qq_bot && python jm_bot.py
```

## 🚀 服务器部署步骤

### 1. 上传文件到服务器

```bash
# 使用 rsync (推荐)
rsync -avz my_website/ user@your-server:/var/www/my_website/
```

### 2. 安装依赖

```bash
# 创建虚拟环境
cd /var/www/my_website
python3 -m venv venv
source venv/bin/activate

# 安装所有依赖
pip install flask flask-sqlalchemy flask-login flask-mail redis apscheduler
pip install jmcomic aiohttp google-genai websockets
pip install gunicorn
```

### 3. 配置 Nginx

```bash
# 复制配置文件
sudo cp /var/www/my_website/nginx.conf.example /etc/nginx/sites-available/my_website

# 编辑配置文件，替换域名和路径
sudo nano /etc/nginx/sites-available/my_website

# 创建软链接启用站点
sudo ln -s /etc/nginx/sites-available/my_website /etc/nginx/sites-enabled/

# 测试配置
sudo nginx -t

# 重启 Nginx
sudo systemctl reload nginx
```

### 4. 使用 Gunicorn 运行 Flask 应用

**运行新版项目 (端口 5001):**
```bash
cd /var/www/my_website/new/myproject
gunicorn -w 4 -b 127.0.0.1:5001 app:app
```

**运行旧版项目 (端口 5002):**
```bash
cd /var/www/my_website/old
gunicorn -w 4 -b 127.0.0.1:5002 app:app
```

**运行JM漫画查看器 (端口 5003):**
```bash
cd /var/www/my_website/qq_bot
gunicorn -w 2 -b 127.0.0.1:5003 jm_bot:app --timeout 300
```

### 5. 使用 Systemd 管理服务 (推荐)

创建服务文件让应用开机自启：

**/etc/systemd/system/website-new.service:**
```ini
[Unit]
Description=New Website (AI运动评估系统)
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory=/var/www/my_website/new/myproject
Environment="PATH=/var/www/my_website/venv/bin"
ExecStart=/var/www/my_website/venv/bin/gunicorn -w 4 -b 127.0.0.1:5001 app:app
Restart=always

[Install]
WantedBy=multi-user.target
```

**/etc/systemd/system/website-old.service:**
```ini
[Unit]
Description=Old Website (经典版)
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory=/var/www/my_website/old
Environment="PATH=/var/www/my_website/venv/bin"
ExecStart=/var/www/my_website/venv/bin/gunicorn -w 4 -b 127.0.0.1:5002 app:app
Restart=always

[Install]
WantedBy=multi-user.target
```

**/etc/systemd/system/jm-bot.service:**
```ini
[Unit]
Description=JM Comic Viewer + QQ Bot
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory=/var/www/my_website/qq_bot
Environment="PATH=/var/www/my_website/venv/bin"
ExecStart=/var/www/my_website/venv/bin/gunicorn -w 2 -b 127.0.0.1:5003 jm_bot:app --timeout 300
Restart=always

[Install]
WantedBy=multi-user.target
```

**启动服务:**
```bash
sudo systemctl daemon-reload
sudo systemctl enable website-new website-old jm-bot
sudo systemctl start website-new website-old jm-bot
```

### 6. 配置 HTTPS (强烈推荐)

使用 Let's Encrypt 免费证书：

```bash
# 安装 Certbot
sudo apt install certbot python3-certbot-nginx

# 获取证书并自动配置 Nginx
sudo certbot --nginx -d your-domain.com
```

## 🔧 常用命令

```bash
# 查看服务状态
sudo systemctl status website-new
sudo systemctl status website-old
sudo systemctl status jm-bot

# 重启服务
sudo systemctl restart website-new website-old jm-bot

# 查看日志
sudo journalctl -u website-new -f
sudo journalctl -u jm-bot -f

# 重启 Nginx
sudo systemctl reload nginx
```

## 🌐 访问地址

部署完成后：
- 根页面导航: `http://your-domain.com/`
- 新版网站: `http://your-domain.com/new/`
- 旧版网站: `http://your-domain.com/old/`
- JM漫画查看器: `http://your-domain.com/jm/`

## 🤖 QQ机器人配置

1. **配置回调地址**: 在QQ开放平台设置机器人回调URL为:
   ```
   https://your-domain.com/qq/callback
   ```

2. **修改配置**: 编辑 `qq_bot/jm_bot.py`，更新以下配置:
   ```python
   SERVER_DOMAIN = "https://your-domain.com/jm"  # 你的服务器域名
   ```

3. **QQ机器人使用方法**:
   - 在QQ群中@机器人发送: `/jm 漫画ID`
   - 例如: `/jm 123456`
   - 机器人会返回漫画前3张预览图和完整阅读链接

## ⚠️ 注意事项

1. **修改域名**: 将 `nginx.conf.example` 中的 `your-domain.com` 替换为你的实际域名
2. **修改路径**: 将配置中的 `/path/to/my_website` 替换为实际部署路径
3. **防火墙**: 确保服务器防火墙开放 80 和 443 端口
4. **数据库**: 如果项目使用数据库，需要提前配置好数据库连接
