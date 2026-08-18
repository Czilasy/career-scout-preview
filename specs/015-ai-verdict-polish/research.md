# Research: Spec 015

本文件为实现后补录的查证记录。

- `call_ai` 统一处理流式响应；HTTP 200 空内容可在解析前识别为 `empty_response`。
- `invalid_response` 的传输层策略可以复用现有按错误码的 retry manifest；JSON 非空垃圾必须继续走 `json_decode`，不能复用空响应策略。
- `profile_facts` 继续作为 `screening_runs` JSON 快照，不新增数据库列；新契约放在 `webui/profile_facts.py`。
- 提示词文本从 `webui/ai.py` 拆出，组装和纯文本分别由 `webui/ai_prompts.py`、`webui/prompt_texts.py` 承担。
- 判定卡片布局只需要组件容器和现有全局样式，不改变后端 verdict 数据结构。
- 直接执行 `scripts/boss_cdp_raw.py` 时 Python 默认只把 `scripts/` 放入 `sys.path`；仓库根目录 bootstrap 是 CLI 自洽入口所需。
