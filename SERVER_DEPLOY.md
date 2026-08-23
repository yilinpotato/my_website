# 🚀 服务器部署教程 - potatoma.me

## 📋 部署概览

| 服务 | 端口 | 路径 | 说明 |
|------|------|------|------|
| 导航页 | - | / | 静态HTML |
| New网站 | 5001 | /new/ | Flask应用 (俯卧撑分析功能需要 torch，可选) |
| Old网站 | 5002 | /old/ | Flask应用 |
| JM漫画机器人 | 5003 | /jm/, /qq/callback | Flask+QQ机器人 |

---

## 1️⃣ 从 GitHub 克隆项目到服务器

### SSH 连接服务器
```bash
ssh root@你的服务器IP
# 或通过宝塔面板的 "终端" 功能
```

### 克隆项目
```bash
# 进入网站目录
cd /www/wwwroot/potatoma.me

# 如果目录已存在其他文件，先备份
mv my_website my_website_backup 2>/dev/null

# 从 GitHub 克隆
git clone https://github.com/yilinpotato/my_website.git

# 进入项目目录
cd my_website
```

### 如果需要更新项目
```bash
cd /www/wwwroot/potatoma.me/my_website
git pull origin main

# 重启服务
systemctl restart potatoma-new potatoma-old potatoma-jm
```

---

## 2️⃣ Python 环境配置 (使用 Miniconda)

### 使用现有的 Miniconda 环境
服务器已安装 Miniconda 在 `/root/miniconda3`，直接使用它安装依赖：

```bash
# 安装所有必需依赖
/root/miniconda3/bin/pip install flask gunicorn redis psutil flask-mail flask-sqlalchemy flask-login werkzeug apscheduler jmcomic aiohttp pynacl google-genai

# 可选：安装 torch (需要较大磁盘空间，约2GB)
# 如果磁盘空间不足，可以跳过此步骤，俯卧撑分析功能将被禁用
# /root/miniconda3/bin/pip install torch --index-url https://download.pytorch.org/whl/cpu
```

### 验证安装
```bash
/root/miniconda3/bin/python -c "import flask, redis, jmcomic; print('All OK')"
```

---

## 3️⃣ Redis 安装 (必需)

### 通过宝塔面板安装
1. 打开宝塔面板
2. 软件商店 -> 搜索 "Redis"
3. 安装 Redis
4. 确保 Redis 运行在 `127.0.0.1:6379`

### 验证
```bash
redis-cli ping  # 应返回 PONG
```

---

## 4️⃣ 创建 Systemd 服务文件

### 创建 New 网站服务
```bash
cat > /etc/systemd/system/potatoma-new.service << 'EOF'
[Unit]
Description=Potatoma New Website
After=network.target redis.service

[Service]
User=root
Group=root
WorkingDirectory=/www/wwwroot/potatoma.me/my_website/new/myproject
ExecStart=/root/miniconda3/bin/gunicorn -w 2 -b 127.0.0.1:5001 app:app
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
```

### 创建 Old 网站服务
```bash
cat > /etc/systemd/system/potatoma-old.service << 'EOF'
[Unit]
Description=Potatoma Old Website
After=network.target redis.service

[Service]
User=root
Group=root
WorkingDirectory=/www/wwwroot/potatoma.me/my_website/old
ExecStart=/root/miniconda3/bin/gunicorn -w 2 -b 127.0.0.1:5002 app:app
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
```

### 创建 JM机器人服务
```bash
cat > /etc/systemd/system/potatoma-jm.service << 'EOF'
[Unit]
Description=Potatoma JM Comic Bot
After=network.target

[Service]
User=root
Group=root
WorkingDirectory=/www/wwwroot/potatoma.me/my_website/qq_bot
ExecStart=/root/miniconda3/bin/gunicorn -w 2 -b 127.0.0.1:5003 jm_bot:app
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
```

### 启动所有服务
```bash
# 重新加载 systemd
systemctl daemon-reload

# 启用开机自启
systemctl enable potatoma-new potatoma-old potatoma-jm

# 启动服务
systemctl start potatoma-new potatoma-old potatoma-jm

# 查看状态
systemctl status potatoma-new potatoma-old potatoma-jm --no-pager
```

---

## 5️⃣ Nginx 配置 (宝塔面板)

### ⚠️ 重要：删除代理配置文件中的重复 location

首先检查并删除代理配置文件中的 `location = /` 规则（如果存在）：

```bash
# 查看代理配置
cat /www/server/panel/vhost/nginx/proxy/potatoma.me/*.conf

# 如果有重复的 location = /，需要删除它
# 或者直接清空代理配置，只在主配置中设置
```

### 在主配置文件中添加以下内容

编辑 `/www/server/panel/vhost/nginx/potatoma.me.conf`，在 `server { }` 块内添加：

```nginx
    # ==================== 自定义反向代理配置 ====================
    
    # ===== 导航首页 =====
    location = / {
        root /www/wwwroot/potatoma.me/my_website;
        index index.html;
    }
    
    # ===== New 网站 =====
    location /new/ {
        proxy_pass http://127.0.0.1:5001/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Prefix /new;
    }
    
    # ===== New 静态文件 =====
    location /new/static/ {
        alias /www/wwwroot/potatoma.me/my_website/new/myproject/static/;
    }
    
    # ===== Old 网站 =====
    location /old/ {
        proxy_pass http://127.0.0.1:5002/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Prefix /old;
    }
    
    # ===== Old 静态文件 (★重要：解决CSS加载问题) =====
    location /old/static/ {
        alias /www/wwwroot/potatoma.me/my_website/old/static/;
    }
    
    # ===== JM漫画查看器 =====
    location /jm/ {
        proxy_pass http://127.0.0.1:5003/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
    
    # ===== QQ机器人回调 =====
    location /qq/callback {
        proxy_pass http://127.0.0.1:5003/qq/callback;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
    
    # ==================== 自定义反向代理配置结束 ====================
```

### 测试并重载 Nginx
```bash
nginx -t && nginx -s reload
```

---

## 6️⃣ 创建必要目录和权限

```bash
# 创建下载目录
mkdir -p /www/wwwroot/potatoma.me/my_website/qq_bot/jm_downloads

# 设置权限
chown -R root:root /www/wwwroot/potatoma.me/my_website
chmod -R 755 /www/wwwroot/potatoma.me/my_website
```

---

## 7️⃣ QQ机器人配置

### 修改配置信息
编辑 `/www/wwwroot/potatoma.me/my_website/qq_bot/jm_bot.py`，确认以下配置：

```python
QQ_APP_ID = "102828452"           # 你的QQ机器人AppID
QQ_APP_SECRET = "你的AppSecret"   # 你的AppSecret (需要填写)
SERVER_DOMAIN = "https://potatoma.me/jm"  # 域名
```

### 在QQ开放平台配置回调地址
1. 登录 [QQ开放平台](https://q.qq.com/)
2. 找到你的机器人应用
3. 设置回调地址为：`https://potatoma.me/qq/callback`

---

## 8️⃣ 一键部署脚本

创建一键部署脚本：

```bash
cat > /www/wwwroot/potatoma.me/my_website/deploy.sh << 'EOF'
#!/bin/bash
echo "🚀 开始部署 potatoma.me..."

# 更新代码
cd /www/wwwroot/potatoma.me/my_website
git pull origin main

# 重启所有服务
systemctl restart potatoma-new potatoma-old potatoma-jm

# 重启 Nginx
nginx -s reload

echo "✅ 部署完成！"
echo "📊 服务状态："
systemctl status potatoma-new --no-pager -l | head -5
systemctl status potatoma-old --no-pager -l | head -5
systemctl status potatoma-jm --no-pager -l | head -5
EOF

chmod +x /www/wwwroot/potatoma.me/my_website/deploy.sh
```

以后更新只需运行：
```bash
/www/wwwroot/potatoma.me/my_website/deploy.sh
```

---

## 📋 常用命令速查

| 操作 | 命令 |
|------|------|
| 查看New服务日志 | `journalctl -u potatoma-new -f` |
| 查看Old服务日志 | `journalctl -u potatoma-old -f` |
| 查看JM服务日志 | `journalctl -u potatoma-jm -f` |
| 重启New服务 | `systemctl restart potatoma-new` |
| 重启Old服务 | `systemctl restart potatoma-old` |
| 重启JM服务 | `systemctl restart potatoma-jm` |
| 重启全部服务 | `systemctl restart potatoma-new potatoma-old potatoma-jm` |
| 重启Nginx | `nginx -s reload` |
| 从GitHub更新 | `cd /www/wwwroot/potatoma.me/my_website && git pull` |
| 检查端口监听 | `ss -tlnp \| grep -E "500[123]"` |

---

## ✅ 验证部署

部署完成后，访问以下地址测试：

| 页面 | 地址 |
|------|------|
| 导航首页 | https://potatoma.me/ |
| New网站 | https://potatoma.me/new/ |
| Old网站 | https://potatoma.me/old/ |
| JM漫画 | https://potatoma.me/jm/ |
| JM漫画查看 | https://potatoma.me/jm/view/123456 |

---

## ❓ 常见问题

### 502 Bad Gateway
- 检查对应服务是否运行：`systemctl status potatoma-*`
- 查看日志：`journalctl -u potatoma-new -n 50`

### CSS/静态文件404
确保 nginx 配置中有静态文件的 alias 规则：
```nginx
location /old/static/ {
    alias /www/wwwroot/potatoma.me/my_website/old/static/;
}
```

### nginx 配置重复 location 错误
```bash
# 检查是否有重复配置
nginx -t

# 如果报错 "duplicate location"，需要删除代理配置文件中的重复规则
# 查看代理配置
cat /www/server/panel/vhost/nginx/proxy/potatoma.me/*.conf
```

### pip 安装失败
```bash
/root/miniconda3/bin/pip install --upgrade pip
/root/miniconda3/bin/pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### Git pull 冲突
```bash
git stash
git pull origin main
git stash pop
```

### 俯卧撑分析功能不可用
这是因为服务器未安装 torch（需要约2GB空间）。如果需要此功能：
```bash
/root/miniconda3/bin/pip install torch --index-url https://download.pytorch.org/whl/cpu
systemctl restart potatoma-new
```
