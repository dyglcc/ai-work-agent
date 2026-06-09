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

图片/海报生成：

```env
# 不配置时使用本地海报兜底渲染；配置后优先调用真实图片生成 API
AI_WORK_IMAGE_API_URL=https://api.example.com/v1/images/generations
AI_WORK_IMAGE_API_KEY=your-image-api-key
AI_WORK_IMAGE_MODEL=dall-e-3
```

支持 OpenAI 风格图片接口返回的 `data[0].url` 或 `data[0].b64_json`。DeepSeek 只负责文字理解，本身不提供图片生成；需要更高质量图片时请单独配置图片生成服务。

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
AI_WORK_SKILLS_DIR=skills
```

`AI_WORK_FILE_STORAGE_DIR` 为空时会使用当前系统的临时目录，因此 Windows 和 macOS 都可以正常运行。

## 可安装 Skills

项目支持本地可安装 Skill。把 Codex/Claude 风格的 Skill 文件夹放进 `skills/`，或在管理后台上传 Skill ZIP，然后点击“功能管理 → 可安装 Skills → 重新扫描”。

最小 Skill 结构：

```text
skills/
  my_skill/
    SKILL.md
```

可选代码型 Skill：

```text
skills/
  my_skill/
    skill.json
    SKILL.md
    handler.py
```

没有 `handler.py` 时，系统会把 `SKILL.md` 当作 system prompt 调用当前 LLM；有 `handler.py` 时，会执行其中的 `handle(message, context)`。

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
  core/              AI 引擎、路由、消息模型、工作流、多租户
  features/          功能模块（12个）
  platforms/         钉钉/飞书平台适配器
  services/          RAG、文件生成、历史记录、文档解析等服务
static/              Web 页面（主页 + 管理后台）
tests/               测试用例
```

## 功能模块

| 模块 | 说明 |
|------|------|
| 通用助手 | 通用问答/对话 |
| 日报周报 | 生成日报/周报/月报等总结报告 |
| 会议纪要 | 整理会议内容，生成会议纪要 |
| 翻译 | 多语言翻译 |
| 代码助手 | 代码生成、解释、优化 |
| 图表生成 | 数据可视化图表生成 |
| 邮件编辑 | 邮件起草和编辑 |
| 智能提醒 | 定时提醒任务 |
| PPT生成 | 演示文稿生成 |
| 图片生成 | AI 图片生成 |
| 内容总结 | 文本内容总结 |
| 项目管理 | WBS 拆解与排期偏差预警 |

## API 端点

### 聊天接口
- `POST /chat` - HTTP 聊天
- `POST /recall` - 撤回上一轮对话
- `POST /upload` - 上传附件
- `POST /transcribe` - 语音转写

### 工作流
- `GET /workflow/agents` - 列出所有 Agent
- `GET /workflow/workflows` - 列出所有工作流
- `POST /workflow/execute` - 执行预定义工作流
- `POST /workflow/dynamic` - 动态编排工作流

### RAG 知识库
- `POST /rag/documents` - 添加文档
- `GET /rag/documents` - 列出文档
- `GET /rag/stats` - 统计信息
- `POST /rag/search` - 检索
- `POST /rag/context` - 获取上下文
- `DELETE /rag/documents/{doc_id}` - 删除文档
- `POST /rag/clear` - 清空知识库
- `POST /rag/chat` - RAG 增强聊天

### 项目管理
- `POST /project/wbs` - WBS 拆解
- `GET /project/history` - 历史记录

### 历史搜索
- `POST /history/search` - 搜索历史
- `GET /history/search` - 搜索历史 (GET)
- `GET /history/stats` - 统计信息
- `DELETE /history/{category}` - 清空分类
- `DELETE /history/record/{category}/{record_id}` - 删除记录

### 管理后台
- `GET /admin` - 管理后台页面
- `GET /admin/features` - 功能列表
- `POST /admin/features/toggle` - 切换功能开关
- `GET /admin/logs` - 系统日志
- `GET /admin/tenants` - 租户列表
- `POST /admin/tenants` - 创建租户
- `GET /admin/tenants/{tenant_id}` - 租户详情
- `PUT /admin/tenants/{tenant_id}` - 更新租户
- `DELETE /admin/tenants/{tenant_id}` - 删除租户

### 其他
- `GET /health` - 健康检查
- `GET /files/{file_id}` - 下载文件
- `GET /reminders` - 提醒列表
- `GET /reminders/triggered` - 已触发提醒
- `GET /parse/supported` - 支持的文档类型
- `POST /parse` - 解析文档
- `POST /parse/and/index` - 解析并索引文档
