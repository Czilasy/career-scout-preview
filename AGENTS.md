# Career Scout 发布卫生规则

本仓库公开。以下规则约束所有人与所有 AI 代理。`AGENTS.md` 是项目规则唯一权威；`CONTRIBUTING.md` 面向人类贡献者，`packaging/README.md` 面向打包发布操作，二者只保留各自特有内容并引用本文件。

## 设计前必读

- 本地开发设计新东西前，先查看 `roadmap/` 下本地参考文档（如 `REFERENCE_GET_JOBS.md`；该目录仅本地存在且已 `.gitignore`，公开仓库不含）。有可借鉴零件就借鉴，没有现成方案再自由发挥。

## 提交与推送

- 提交或推送前先运行：`uv run python -m unittest tests.test_repo_hygiene`，失败禁止提交和推送。
- 查看 `git status` 与 `git diff --cached`，确认没有无关文件、临时文件、密钥或不应提交产物。
- 提交信息使用 Conventional Commits（`feat|fix|docs|style|refactor|test|perf|build|ci|chore|revert`）；卫生测试校验最近 3 条非 merge 提交格式。

## 钩子

- `hooks/pre-commit`：卫生测试 + 暂存区 whitespace 检查，失败阻断提交。
- `hooks/pre-push`：卫生测试 + 待推送差异 + 提交身份 + 前端 dist 同步检查，失败阻断推送；前端检查只查不建，dist 不一致先手动构建并提交。
- 启用方式：`git config core.hooksPath hooks`；卫生测试校验该配置已启用。

## 文件边界

- 不提交真实 Key、Cookie、密码、本地绝对路径、旧账号名或旧项目名。
- 本地运行数据、缓存、构建产物、测试生成物、凭据文件一律加入 `.gitignore`，禁止 `git add -f`。
- 新增文件先判断是否公开、是否对别人运行项目必要；`git status` 出现意外新文件时优先处理，不要直接提交。

## 提交身份

- 作者与提交者邮箱必须为 `czyooutzilas@gmail.com`。

## 版本与发布

- 版本提升必须用 `scripts/bump_version.py`，同步：`pyproject.toml`、`webui/package.json`、`webui/package-lock.json`、`uv.lock`、`scripts/boss_cdp_raw.py`、`tests/test_desktop_shell.py`、`README.md` 标题，并生成 CHANGELOG 条目。
- 语义：patch=bug 修复/文案/样式等小改动；minor=新功能且向后兼容；major=重构/大功能/里程碑。
- 构建产物命名 `CareerScout-v<version>.*`；`.release/` 已有同名产物时，`packaging/build_exe.ps1` 必须显式传 `-Force` 才允许覆盖。
- 上传 Windows EXE 后必须推送 `v*` tag，触发 `.github/workflows/release-macos.yml` 构建 dmg；发布后核对该 tag 的 Release 上 EXE、DMG 及各自 `.sha256` 均已挂载。

## 文档卫生

- README 等说明文档随功能/行为变化实时更新；新增或变更用户可感知能力时，先检查相关文档是否需要同步。
- 更新说明（GitHub Release Notes 与 CHANGELOG 条目）只允许简单列表：
  修复：
  -xxx
  增加：
  -xxx
  优化：
  -xxx
- 条目正文禁止标题（`##` / `###`）、加粗等符号，禁止英文术语与晦涩字段名；一句话说清用户可感知改动。CHANGELOG 保留 `## [版本号]` 标题用于定位。

## 测试输出纪律

- 测试日志、输出重定向禁止写入项目根目录，一律使用系统临时目录（`$env:TEMP`）。
- 自产临时文件（日志、缓存、测试产物）当轮清理；发现根目录出现中转文件立即删除，不得以已被 `.gitignore` 忽略为由保留。
