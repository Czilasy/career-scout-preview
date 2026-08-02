# AGENTS.md

指引给在 Career Scout 仓库中工作的 AI coding agent。先读这份，再动代码。

## 这是什么

`career-scout` —— 通过 Chrome CDP（远程调试端口）连接用户本人已登录的 Chrome，抓取 BOSS直聘的公开职位数据（列表 + 详情），生成求职分析摘要，并提供 WebUI 求职工作台。仅用于个人求职分析，不是大规模爬虫（见 `CONTRIBUTING.md` 的合规一节）。

## 目录结构

```
scripts/boss_cdp_raw.py   # CLI 抓取主入口
scripts/job_summary.py    # 抓取结果 → Markdown 求职分析摘要
data/city_codes.json      # 城市码表（300+ 城市）
webui/app.py              # Flask WebUI + 后台任务编排
webui/src/                # Vue 3 + TypeScript 前端源码
webui/dist/               # 已提交的生产构建产物
tests/                    # unittest，全 mock，不依赖真实 Chrome/网络
tools/start.bat           # Windows 一键启动 WebUI
pyproject.toml            # hatchling 打包；入口 career-scout / career-summary
requirements.txt          # 运行时依赖
SKILL.md / README(.en).md / CHANGELOG.md / CONTRIBUTING.md / SECURITY.md / CODE_OF_CONDUCT.md
```

`webui/dist/` 是提交到仓库的生产构建，普通用户无需 Node.js；修改前端源码后必须执行 `npm run build` 并提交新产物。

## 环境与命令

- Python >=3.10，依赖见 `requirements.txt`。可用 `uv sync` 或 `pip install -r requirements.txt`。
- Python 测试：`python -m unittest discover -s tests -p "test_*.py"`（macOS/Linux 将 `python` 换成 `python3`）。
- 前端测试与构建：`cd webui && npm install && npm test && npm run build`。
- 语法自检：`python -m py_compile scripts/boss_cdp_raw.py`。
- WebUI 启动：`python webui/app.py`（Windows 可直接用 `tools/start.bat`），访问 `http://127.0.0.1:5000`，根路径 `/` 是唯一正式前端入口。
- 实跑抓取需要先启动带调试端口的 Chrome：`python scripts/boss_cdp_raw.py --setup-chrome`（默认 `127.0.0.1:9222`），登录后在另一个终端跑抓取命令。

## 改代码时的硬规则

1. 版本号四处一致：`scripts/boss_cdp_raw.py` 的 `__version__`、`pyproject.toml`、`SKILL.md`、`README.md`，否则 `VersionConsistencyTests` 会挂。
2. 禁止 bare `except:`，必须捕获具体类型，和现有代码保持一致。
3. 改了用户可见行为 → 更新 `README.md`；有意义变更 → `CHANGELOG.md` 顶部加一条。
4. `README.md` 与 `README.en.md` 保持双语同步。
5. commit message 用 Conventional Commits（`feat:` / `fix:` / `docs:` / `refactor:` 等，见 `CONTRIBUTING.md`）。
6. 重启 WebUI 前先停掉占用 5000 端口的旧进程，再启动新进程并确认端口在听；只启动不杀旧进程等于没改。

## 架构关键点

- `scripts/boss_cdp_raw.py` 是长单文件，包含 CDP 会话、注入脚本、列表抓取、详情抓取和 CLI 入口；城市码表外置到 `data/city_codes.json`。
- 列表页与详情页路径不同：列表页走页面内 `fetch` 调 BOSS wapi；详情页走新开 tab → 注入 JS 提取。改其中一条路径时，另一条不受影响。
- CDP `background:true` 的坑：后台 tab 的 `document.hidden=true` 会触发 BOSS visibility 反爬，当前在详情抓取前注入脚本覆盖可见性属性。动详情逻辑时别破坏这段。
- 同一 Chrome 实例的默认 browser context 下，新开 target 共享 cookies。
- `webui/store.py` / `webui/workbench.py` / `webui/source.py` 是工作台核心；详情抓取、AI 合同校验、任务状态机改动必须跑对应 `tests/test_webui_*.py` 和 `tests/test_healthy_pipeline.py` 回归。

## 提交流程

默认分支 `master`，单主线提交，不创建 feature 分支。commit message 用 Conventional Commits。
