# AI Work Agent 开发约定

本文件是 Codex 的项目规则入口。Claude Code 使用同目录下的 `CLAUDE.md`。两份文件的项目约束应保持一致。

## 项目概况

这是一个基于 FastAPI 的 AI 工作助手，支持 Web 聊天、钉钉/飞书适配、PPT/Word/图表生成、RAG 知识库和项目管理接口。

主要入口：

- 应用入口：`app/main.py`
- 启动入口：`run.py`
- 配置入口：`app/config.py`
- Web 页面：`static/index.html`
- 管理后台：`static/admin.html`

## 双平台兼容要求

本项目由 macOS 和 Windows 开发者共同维护。后续所有代码、脚本、文档和测试都必须兼容两个平台。

强制规则：

- 不要写死 macOS 路径，例如 `/Users/...`、`/tmp/...`。
- 不要写死 Windows 路径，例如 `C:\...`、反斜杠拼接路径。
- Python 里统一使用 `pathlib.Path`、`tempfile.gettempdir()`、`os.environ` 等跨平台 API。
- 临时文件默认使用系统临时目录；需要自定义时走 `AI_WORK_FILE_STORAGE_DIR`。
- 环境变量统一使用 `AI_WORK_` 前缀，并通过 `app.config.settings` 读取。
- 线上版本使用千问网关：`AI_WORK_AI_PROVIDER=anthropic`、`AI_WORK_CLAUDE_MODEL=qwen3.6-plus`。
- 本地 debug 可以使用 DeepSeek：复制 `.env.debug.example` 到 `.env`，设置 `AI_WORK_AI_PROVIDER=openai` 和 `AI_WORK_OPENAI_API_KEY`。
- shell 脚本和 PowerShell 脚本要成对维护：macOS/Linux 使用 `.sh`，Windows 使用 `.ps1`。
- README 里的启动、检查、测试命令必须同时给出 Windows PowerShell 和 macOS/Linux bash 版本。
- 新增依赖或启动方式时，必须验证 Windows 和 macOS 都能按文档执行。

## 启动方式

Windows PowerShell：

```powershell
.\start.ps1
```

macOS/Linux：

```bash
./start.sh
```

直接启动：

```bash
python run.py
```

启动后访问：

- Web 页面：http://localhost:8000
- 管理后台：http://localhost:8000/admin
- 健康检查：http://localhost:8000/health

## 开发约定

- Python 版本要求：3.9+
- FastAPI 应用代码优先放在 `app/` 下，遵循现有模块边界。
- 新 API 优先放在 `app/main.py`，除非已经明显需要拆分模块。
- Feature 模块继承 `app.features.base.Feature`。
- 使用 `async/await` 处理异步逻辑。
- 不要把密钥写入仓库；真实密钥只放 `.env`。
- `.env.example` 只放占位值和默认配置。

## 验证命令

```bash
python -m py_compile run.py app/config.py app/main.py
python -m pytest
```

Windows 文件生成检查：

```powershell
.\test_file_output.ps1
```

macOS/Linux 文件生成检查：

```bash
./test_file_output.sh
```
