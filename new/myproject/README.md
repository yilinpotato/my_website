# AI运动评估系统 - 本地运行指南

本文档指导您如何在本地环境中配置并运行本项目。

## 1. 环境准备

确保您的电脑上已安装 Python (建议 3.8 或更高版本)。

## 2. 创建虚拟环境

为了避免依赖冲突，建议使用虚拟环境。

**Windows (PowerShell):**
```powershell
# 创建虚拟环境
python -m venv venv

# 激活虚拟环境
.\venv\Scripts\Activate.ps1
```

**macOS / Linux:**
```bash
# 创建虚拟环境
python3 -m venv venv

# 激活虚拟环境
source venv/bin/activate
```

## 3. 安装依赖

激活虚拟环境后，运行以下命令安装项目所需的依赖包：

```bash
pip install -r requirements.txt
```

## 4. 运行项目

依赖安装完成后，使用以下命令启动 Flask 应用：

```bash
python app.py
```

或者，如果您配置了 Flask 环境变量：

```bash
flask run
```

## 5. 访问网页

项目启动后，通常会运行在 5000 端口。打开浏览器访问：

[http://127.0.0.1:5000](http://127.0.0.1:5000)

## 6. 配置环境变量

复制 `.env.example` 为 `.env`，再填写本地配置。请勿将真实密钥、邮箱授权码、管理员口令或 Cookie 提交到 Git。

Gemini 与邮件功能属于可选功能；未配置对应变量时，基础页面和不依赖外部服务的功能仍可运行。

## 7. 管理员账户

如果项目中包含管理员功能，请参考 `app.py` 中的配置或数据库初始化脚本来获取或创建管理员账户。

## 常见问题

- **Redis 连接错误**: 如果项目依赖 Redis，请确保本地已安装并启动了 Redis 服务，或者在 `app.py` 中修改 Redis 配置。
- **缺少模块**: 如果运行报错提示 `ModuleNotFoundError`，请检查是否遗漏了安装某些库，可以尝试手动安装，例如 `pip install <module_name>`。
