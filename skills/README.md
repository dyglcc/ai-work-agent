# AI Work Agent Skills

把可安装技能放在这个目录下，每个技能一个文件夹。

最小结构：

```text
skills/
  my_skill/
    SKILL.md
```

可选结构：

```text
skills/
  my_skill/
    skill.json
    SKILL.md
    handler.py
```

`SKILL.md` 可以直接从 Codex/Claude 的说明型 skill 拷贝过来。没有 `handler.py` 时，系统会把 `SKILL.md` 作为 system prompt 调用当前 LLM。

`skill.json` 示例：

```json
{
  "id": "my_skill",
  "name": "我的技能",
  "description": "描述这个技能做什么",
  "keywords": ["我的技能", "触发词"],
  "entry": "handler.py",
  "enabled": true
}
```

`handler.py` 示例：

```python
async def handle(message, context):
    ai = context["ai"]
    return await ai.chat(message.content, "你是一个专业助手", user_id=message.user_id)
```

代码型 Skill 可以使用项目内置工具：

```python
async def handle(message, context):
    web_search = context["tools"]["web_search"]
    results = await web_search("USD to CNY exchange rate", max_results=3)
    return str(results)
```

当前内置工具：

- `web_search(query, max_results=5)`：搜索网页，返回标题、链接、摘要。
- `web_fetch(url, max_chars=4000)`：抓取网页正文。

后台支持两种安装方式：

- 上传本地 Skill ZIP。
- 输入网络 `.zip` URL 安装。
