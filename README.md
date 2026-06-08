# AI Work Agent

一个基于 FastAPI 的 AI 工作助手，支持 Web 聊天、钉钉/飞书适配、PPT/Word/图表生成、RAG 知识库和项目管理接口。

## 环境要求

- Python 3.9+
- Windows PowerShell 或 macOS/Linux bash

## 安装依赖

```bash
pip install -e .
```

测试依赖：

```bash
pip install -e ".[test]"
```

## 配置环境变量

复制示例配置：

```bash
cp .env.example .env
```

Windows PowerShell：

```powershell
Copy-Item .env.example .env
```

配置项使用 `AI_WORK_` 前缀，例如：

```env
AI_WORK_AI_PROVIDER=anthropic
AI_WORK_ANTHROPIC_API_KEY=your_api_key
AI_WORK_CLAUDE_MODEL=claude-sonnet-4-20250514
AI_WORK_DINGTALK_ENABLED=false
AI_WORK_FEISHU_ENABLED=false
```

本地 debug 使用 DeepSeek：

```powershell
Copy-Item .env.debug.example .env
```

macOS/Linux：

```bash
cp .env.debug.example .env
```

然后把 `.env` 里的 `AI_WORK_OPENAI_API_KEY` 改成你的 DeepSeek API Key。

线上版本使用千问网关：

```powershell
Copy-Item .env.production.example .env
```

macOS/Linux：

```bash
cp .env.production.example .env
```

## 启动服务

macOS/Linux：

```bash
./start.sh
```

Windows PowerShell：

```powershell
.\start.ps1
```

也可以直接运行：

```bash
python run.py
```

启动后访问：

- Web 页面：http://localhost:8000
- 管理后台：http://localhost:8000/admin
- 健康检查：http://localhost:8000/health

## 可选运行参数

```env
AI_WORK_HOST=0.0.0.0
AI_WORK_PORT=8000
AI_WORK_RELOAD=true
AI_WORK_FILE_STORAGE_DIR=
```

`AI_WORK_FILE_STORAGE_DIR` 为空时会使用当前系统的临时目录，因此 Windows 和 macOS 都可以正常运行。

## Docker

```bash
docker compose up -d
```

## 常用检查

macOS/Linux：

```bash
./check_dingtalk.sh
./test_file_output.sh
```

Windows PowerShell：

```powershell
.\check_dingtalk.ps1
.\test_file_output.ps1
```

## 项目结构

```text
app/
  main.py            FastAPI 应用入口
  config.py          配置管理
  core/              AI 引擎、路由、消息模型、工作流
  features/          功能模块
  platforms/         钉钉/飞书平台适配器
  services/          RAG、文件生成、历史记录等服务
static/              Web 页面
tests/               测试用例
```
