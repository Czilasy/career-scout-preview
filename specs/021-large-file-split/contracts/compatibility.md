# Contracts: 大文件拆分重构（021）

## C1: Python 门面契约

- `from webui.app import <任意拆分前公开符号>` MUST 继续可用（含 `boss`、`_BossCdpSource`、`ai_service`、`ScraperExecutor`、`_screen_overall_percent`、`_split_resume_verdicts` 等 monkeypatch 面）。
- `from webui.source import ...`、`from webui.store import ...`、`python scripts/boss_cdp_raw.py <原参数>` 行为不变。

## C2: HTTP API 契约

- 全部既有路由路径、方法、请求/响应字段、错误码不变（由 8000+ 行既有后端测试守护，零改动即为契约验证）。

## C3: 前端契约

- DiscoveryView 模板 DOM 结构、事件绑定、UI 表现不变；composables 为纯搬运，props/emits 面对外仅暴露原逻辑等价接口。
- 既有前端 452 用例零改动通过。

## C4: CLI 契约

- `scripts/boss_cdp_raw.py` 的命令行参数、输出格式、退出码不变；`scripts/boss/` 子模块为其内部实现。
