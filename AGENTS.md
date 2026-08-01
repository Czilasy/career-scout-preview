# AGENTS.md

指引给未来的 ZCode agent。先读这份，再动代码。

## 这是什么

`career-scout` —— 通过 Chrome CDP（远程调试端口）连接**用户本人已登录的 Chrome**，抓取 BOSS直聘的公开职位数据（列表 + 详情），并可生成求职分析摘要。仅用于个人求职分析，非大规模爬虫（见 `CONTRIBUTING.md` 的合规一节）。

## 目录结构

```
scripts/boss_cdp_raw.py   # 核心：抓取 + CLI 主入口（~1900 行，单文件）
scripts/job_summary.py    # 抓取结果 → Markdown 求职分析摘要
data/city_codes.json      # 全量城市码表（300+ 城市，外置；见下）
tests/test_chrome_setup.py    # unittest，全 mock，不依赖真实 Chrome/网络
tests/test_job_summary.py     # 摘要测试
pyproject.toml            # hatchling 打包；入口 career-scout / career-summary
requirements.txt          # 仅 requests + websocket-client
SKILL.md / README(.en).md / CHANGELOG.md / CONTRIBUTING.md
```

**重要边界：核心逻辑都放 `scripts/boss_cdp_raw.py`，不要随手新建文件**（见 `CONTRIBUTING.md`「单文件原则」）。`docs/` 被 `.gitignore` 忽略，是本地产物，不要提交。**例外**：`data/city_codes.json` 是城市码表数据（非逻辑代码），外置便于用户查看支持哪些城市；改它要同步跑 `tests.test_chrome_setup` 的城市码表防回归测试。

## 环境与命令

- Python **>=3.10**，依赖只有 `requests` + `websocket-client`。用项目里的 `.venv`（`source .venv/bin/activate`），别用 pyenv 全局解释器（会缺依赖报错）。
- 包管理用 `uv`（仓库有 `uv.lock`），也可 `pip install -r requirements.txt`。
- 跑测试：`python3 -m unittest tests.test_chrome_setup`（无需 Chrome / 联网，全 mock）。改了 `job_summary` 再加跑 `tests.test_job_summary`。
- 语法自检：`python3 -m py_compile scripts/boss_cdp_raw.py`。
- 实跑抓取需要先启动带调试端口的 Chrome：`python3 scripts/boss_cdp_raw.py --setup-chrome`（开 `127.0.0.1:9222`，默认端口见 `DEFAULT_CDP_PORT`），登录后在**另一个终端**跑抓取命令。Chrome 关了端口就没了。

## 改代码时的硬规则

1. **版本号四处一致**：`scripts/boss_cdp_raw.py` 的 `__version__`（第 22 行附近）、`pyproject.toml`、`SKILL.md`、`README.md` 必须同步，否则 `VersionConsistencyTests` 会挂。改版本号时四处一起改。
2. **异常处理**：禁止 bare `except:`，必须捕获具体类型（`requests.ConnectionError`、`json.JSONDecodeError` 等），和现有代码保持一致。
3. **改了用户可见行为 → 更新 `README.md`；有意义变更 → `CHANGELOG.md` 顶部加一条。**
4. **README 双语同步**：`README.md`（中文）和 `README.en.md`（英文）必须保持一致，改了其中一个就要同步另一个。
5. **commit message 用 Conventional Commits**（`feat:` / `fix:` / `docs:` / `optimize:` / `refactor:` 等，见 `CONTRIBUTING.md`）。
6. **启动或重启 webui 前，先运行 `.venv\Scripts\python.exe webui\ensure_frontend_sync.py` 检查构建状态；后端代码或前端源码与 `webui/dist` 不一致时由脚本自动执行 `npm run build`。改完 webui 前端或后端代码，必须重启 webui 服务并验证旧服务被替换**。Flask debug 模式默认关闭，不会热重载；端口 5000 常被你之前自己启动的旧进程占着，新进程会因端口冲突静默失败。正确流程：①`Get-NetTCPConnection -LocalPort 5000` 查旧 PID → ②`Stop-Process -Id <旧PID> -Force` → ③启动新服务 → ④再 `Get-NetTCPConnection -LocalPort 5000` 确认新 PID 在听 → ⑤浏览器访问确认新代码生效。**只启动不杀旧 = 等于没改**。

## 架构关键点（容易踩坑）

- `scripts/boss_cdp_raw.py` 是一个**长单文件**，包含：`CDPSession` 类（WebSocket 连 CDP）、各种 `EXTRACT_*_JS` 注入脚本、`scrape_jobs`（列表走 `/wapi/...` API）、`scrape_details`（详情走新开 tab 渲染）、`main`（argparse）。城市码表外置到 `data/city_codes.json`，`resolve_city` 查询链为「本地静态码表 → 运行时拉 BOSS 接口 → 原样兜底」。
- **列表页 vs 详情页路径完全不同**：列表页通过页面内 `fetch` 调 BOSS wapi（带 token，不经页面渲染）；详情页通过 `Target.createTarget` 新开 tab → `Page.navigate` → 注入 JS 提取。改其中一条路径时，另一条不受影响。
- **CDP `background:true` 的坑**：后台 tab 的 `document.hidden=true` 会触发 BOSS visibility 反爬，导致详情抓空。当前在 `scrape_details` 导航前用 `Page.addScriptToEvaluateOnNewDocument` 注入脚本覆盖可见性属性为 visible。动详情页逻辑时别破坏这段。
- 同一个 Chrome 实例的默认 browser context 下，新开 target **本就共享 cookies**，不要被「新 tab 丢 cookie」的直觉误导。
- `require_runtime_dependencies("requests", "websocket")` 在多个入口前置检查依赖，缺了会提示安装。

## 提交流程

默认分支 `master`，**单主线提交，不创建 feature 分支**。改动前直接在本会话说明「改什么 / 为什么 / 怎么改 / 影响范围」即可动手。commit message 用 Conventional Commits。

### Push 配置（重要）

- **SSH 密钥**：`~/.ssh/id_ed25519_github`，认证身份 `czyooutzilas-sketch`
- **公钥**：`ssh-ed25519 <your-public-key>`
- **SSH config**：走 `ssh.github.com:443`（HTTPS 端口隧道）
- **Push 目标**：`git@github.com:czyooutzilas-sketch/career-scout-preview.git`（私有仓库，已绑定，`git push` 默认推这里）

## 难点记录（.devlog/）

解决有难度的 bug 或技术难点后（不是改个 CSS 这种小事），**主动提议**将过程记录到项目 `.devlog/` 目录。用户确认后按以下格式写入：

- 文件命名：`YYYY-MM-DD-<简短英文标识>.md`（如 `2026-07-25-source-blocked.md`）
- 固定结构（面试导向）：

```markdown
# 一句话概括

## 现象
用户/产品视角看到了什么（不带技术术语）

## 排查过程
关键步骤、转折点、走过的弯路（体现思考能力）

## 根因
最终定位到的具体原因

## 修复
改了什么、为什么这么改

## 面试话术
一段可以直接对面试官说的完整叙述（2-3 分钟，口语化）

## 延伸知识点
涉及的技术概念，供深入准备
```

判断标准：这个问题是否值得在面试中花 2 分钟讲？如果答案是"是"，就记录。
