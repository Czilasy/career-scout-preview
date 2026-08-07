# 贡献指南

感谢你有兴趣参与 Career Scout。本项目是个人维护的本地求职工具，开发节奏不快，但所有改动都需要通过测试与仓库卫生检查。请在贡献前读完本文件。

## 可以贡献什么

- **Bug 修复**：欢迎。先开 issue 描述复现步骤，确认后再提 PR。
- **文档与界面文案改进**：欢迎，直接提 PR。
- **新功能**：请先开 issue 讨论。本项目有明确的产品边界——不做自动投递、自动联系招聘者、自动标记状态等自动化行为（见 README 定位说明），偏离该边界的提案大概率不会合并。
- **新平台接入**：涉及抓取行为，需先在 issue 中讨论合规与风控影响。

## 开发环境

```bash
git clone https://github.com/Czilasy/career-scout-preview.git
cd career-scout-preview
uv sync            # 或 pip install -r requirements.txt
git config core.hooksPath hooks   # 启用仓库自带的提交钩子（必须）
```

前端（仅在修改 WebUI 时需要）：

```bash
cd webui
npm install
npm test
npm run build
```

## 提交规范

1. **提交信息使用 Conventional Commits**：`feat|fix|docs|style|refactor|test|perf|build|ci|chore|revert`，卫生测试会校验最近 3 条非 merge 提交的格式。
2. **提交或推送前先运行卫生测试**：

   ```bash
   uv run python -m unittest tests.test_repo_hygiene
   ```

   失败时禁止提交，先处理未跟踪文件、忽略规则或敏感文件。
3. **提交前检查** `git status` 和 `git diff --cached`，确认没有把无关文件一起提交。
4. **禁止提交的内容**：真实 API Key、Cookie、密码、本地绝对路径、个人账号信息。本地运行数据、缓存、构建生成物、测试产物一律通过 `.gitignore` 忽略，禁止使用 `git add -f`。
5. 修改前端源码后必须重新构建并提交 `webui/dist`（`hooks/pre-push` 会校验前端构建同步）。

## 版本管理

版本号遵循语义化版本（`MAJOR.MINOR.PATCH`），三处版本位置必须保持一致：

- `pyproject.toml` 的 `version`（产品版本权威源，后端从这里读取）
- `webui/package.json` 的 `version`
- `scripts/boss_cdp_raw.py` 的 `__version__`

递增规则：

| 类型 | 示例 | 适用场景 |
| --- | --- | --- |
| PATCH | `2.7.0 -> 2.7.1` | 小修小补：bug 修复、文案、样式 |
| MINOR | `2.7.x -> 2.8.0` | 新功能（向后兼容） |
| MAJOR | `2.x.y -> 3.0.0` | 重构、超大功能、纪念性版本 |

发布时使用工具一次改齐并生成 CHANGELOG 条目（`-m` 为条目描述，会同时写入
CHANGELOG.md 的「变更」分组，如需「新增/修复」分组请手动调整后再提交）：

```bash
python scripts/bump_version.py patch -m "修复收藏抽屉取消收藏报错"
python scripts/bump_version.py minor -m "新增平台筛选"
python scripts/bump_version.py major -m "2.0 大版本"
```

工具只做版本号与 CHANGELOG 条目，不做 git 提交；请确认 diff 后自行按
Conventional Commits 提交（发布类提交建议使用 `feat:` 或 `fix:` 前缀 + 版本号说明）。

## 测试要求

- 后端测试：`python -m unittest discover -s tests`，全部使用 mock，不得依赖真实 Chrome、真实账号或网络。
- 前端测试：`cd webui && npm test`。
- PR 必须附带覆盖改动的测试；修复 bug 的 PR 必须先添加能复现该 bug 的失败测试。

## PR 流程

1. Fork 本仓库并创建功能分支，分支名建议 `feat/xxx`、`fix/xxx`。
2. 保持 PR 小而聚焦；一个 PR 只做一件事。
3. 在 PR 描述中说明动机、改动范围和测试方式。
4. 确保 CI/本地测试全绿后请求 review。合并采用 squash 方式。

## 代码风格

- Python 3.10+，类型标注优先；测试用 `unittest`。
- 错误处理要如实暴露失败原因（风控、验证码、登录过期、限流等分类明确），不得伪装成功。
- 涉及 AI 的功能必须提供规则式兜底，主链路不依赖 AI 可用性。

## 许可证

提交代码即表示你同意自己的贡献按 [Apache License 2.0](./LICENSE) 授权。
