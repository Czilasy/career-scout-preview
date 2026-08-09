# 贡献指南

感谢你有兴趣参与 Career Scout。项目规则（提交规范、版本管理、发布卫生、发布说明格式）以根目录 `AGENTS.md` 为唯一权威，本文件只写人类贡献者需要知道的流程。

## 可以贡献什么

- **Bug 修复**：欢迎。先开 issue 描述复现步骤，确认后再提 PR。
- **文档与界面文案改进**：欢迎，直接提 PR。
- **新功能**：请先开 issue 讨论。本项目不做自动投递、自动联系招聘者、自动标记状态等行为，偏离产品边界的提案大概率不会合并。
- **新平台接入**：涉及抓取行为，需先在 issue 中讨论合规与风控影响。

## 开发环境

```bash
git clone https://github.com/Czilasy/career-scout-preview.git
cd career-scout-preview
uv sync            # 或 pip install -r requirements.txt
git config core.hooksPath hooks   # 必须，提交/推送门禁依赖
```

前端（仅在修改 WebUI 时需要）：

```bash
cd webui
npm install
npm test
npm run build
```

修改前端源码后必须重新构建并提交 `webui/dist`，否则推送会被 `hooks/pre-push` 拦截。

## 提交

- 提交信息使用 Conventional Commits；提交/推送前先运行卫生测试，并查看 `git status` 与 `git diff --cached`。
- 具体规则见 `AGENTS.md` 的「提交与推送」「文件边界」「提交身份」。

## 测试要求

- 后端测试：`python -m unittest discover -s tests`，全部使用 mock，不得依赖真实 Chrome、真实账号或网络。
- 前端测试：`cd webui && npm test`。
- PR 必须附带覆盖改动的测试；修复 bug 的 PR 必须先添加能复现该 bug 的失败测试。
- CI 门禁：push 到任意远程分支或发起 PR 时，`.github/workflows/ci.yml` 自动运行后端与前端测试，任一失败即标记未通过并阻断合并。
- 本地后端测试建议使用 `uv run python -m unittest discover -s tests`，与 CI 保持一致。

## PR 流程

1. Fork 本仓库并创建功能分支，分支名建议 `feat/xxx`、`fix/xxx`。
2. 保持 PR 小而聚焦；一个 PR 只做一件事。
3. 在 PR 描述中说明动机、改动范围和测试方式。
4. 确保 CI/本地测试全绿后请求 review；合并采用 squash 方式。

## 代码风格

- Python 3.10+，类型标注优先；测试用 `unittest`。
- 错误处理要如实暴露失败原因（风控、验证码、登录过期、限流等分类明确），不得伪装成功。
- 涉及 AI 的功能必须提供规则式兜底，主链路不依赖 AI 可用性。

## 许可证

提交代码即表示你同意自己的贡献按 [Apache License 2.0](./LICENSE) 授权。
