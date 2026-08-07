# Career Scout 发布卫生规则

本仓库是公开仓库，提交或推送后的任何内容都可能被其他人克隆。以下规则适用所有人和所有 AI 代理。

## 设计新功能前必读

- 本地开发时，设计新东西先查看 `roadmap/` 下的本地参考文档（如 `REFERENCE_GET_JOBS.md`；该目录仅本地存在且已被 `.gitignore` 忽略，公开仓库不含）。有可借鉴的零件就借鉴，没有现成方案再自由发挥。

## 提交或推送前必做

- 先运行：`uv run python -m unittest tests.test_repo_hygiene`
- 测试失败时禁止提交和推送，先处理未跟踪文件、忽略规则或敏感文件。
- 查看 `git status` 和 `git diff --cached`，确认没有把无关文件一起提交。
- 提交信息使用 Conventional Commits（`feat|fix|docs|style|refactor|test|perf|build|ci|chore|revert`），卫生测试会校验最近 3 条非 merge 提交的格式。

## 提交钩子（已入库，克隆后启用）

- `hooks/pre-commit` 会在提交前自动运行卫生测试和暂存区差异检查，失败即阻断提交。
- `hooks/pre-push` 会在推送前自动运行卫生测试、待推送差异检查、提交身份检查和前端构建同步检查，失败即阻断推送。
- 启用方式：`git config core.hooksPath hooks`

## 文件边界

- 不提交真实 Key、Cookie、密码、本地绝对路径、旧账号名或旧项目名。
- 本地运行数据、缓存、构建产物、测试生成物、凭据文件一律加入 `.gitignore`，禁止使用 `git add -f`。
- 新增文件前先判断：这个文件是否值得公开？是否对别人运行项目必要？不是就忽略或删除。
- 看到 `git status` 中出现意外的新文件时，优先处理，不要直接提交。

## 提交身份

- 提交作者和提交者邮箱必须使用 `czyooutzilas@gmail.com`。
- 不在公开仓库使用其他账号身份。

## 版本提升（发布前必做）

- 版本只属于软件（桌面应用），必须用 `scripts/bump_version.py` 提升（自动同步 pyproject.toml / webui/package.json / scripts/boss_cdp_raw.py 并生成 CHANGELOG 条目），同步更新 tests/test_desktop_shell.py 的版本断言。
- 语义（对齐 CONTRIBUTING.md 与 bump_version.py）：
  - 小功能（bug 修复、文案、样式等小改动）→ patch：`2.2.5 → 2.2.6`（用户示例 "2.2.xxx"）
  - 中等功能（新功能、向后兼容）→ minor：`2.2.x → 2.3.0`（用户示例 "2.xx.10" 指 minor 位递增）
  - 重构 / 大功能 / 里程碑 → major：`2.x.y → 3.0.0`（用户示例 "xx.1.2" 指主版本递增）
- 构建产物命名 `CareerScout-v<version>.*`；版本号未提升时禁止覆盖 `.release/` 下已有的同名旧产物——先升版本，再构建。

## 跨平台产物（dmg）

- 上传完 Windows EXE 后，必须在 GitHub 构建 dmg：推送 `v*` tag 会自动触发 `.github/workflows/release-macos.yml`（GitHub macOS runner 构建 arm64 dmg，自检后附加到对应 Release）。
- 发布流程必须推送版本 tag，不得跳过 dmg；发布后核对该 tag 的 Release 上 EXE 与 DMG 均已挂载。

## 文档卫生

- README 等说明文档必须随功能/行为变化实时更新，除非确认没有更新必要；新增或变更用户可感知能力时，先检查相关文档是否需要同步。

## 测试输出纪律

- 测试日志、输出重定向禁止写入项目根目录，一律使用系统临时目录（`$env:TEMP`）。
- 自产临时文件（日志、缓存、测试产物）当轮清理，不得遗留；发现根目录出现中转文件立即删除，不得以"已被 gitignore 忽略"为由保留。

## 发布说明格式

- 更新说明（GitHub Release Notes 与 CHANGELOG 条目）只允许简单列表：
  修复：
  -xxx
  增加：
  -xxx
  优化：
  -xxx
- 禁止 Markdown 标题（## / ###）、加粗（**）等符号，禁止英文术语与晦涩字段名；描述用一句话说清用户能感知的改动。
