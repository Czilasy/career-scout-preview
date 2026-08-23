# Quickstart: 大文件拆分重构（021）验证指南

每批次交付后按序执行，全部通过才允许提交。

## 前置

```bash
cd D:/项目/career-scout-preview
```

## 0. 基线（仅开工前一次）

实测全仓全量后端测试入口（以仓内惯例模块式调用为准，如 `uv run python -m unittest discover` 与 `tests.__main__` 实测确认，勿臆断），跑一次全量并记录结果；记录 11 个文件行数清单。**基线必须全绿才开工**——批次红了先对照基线，区分"拆坏"还是"本来就红"。

## 1. 行数红线检查

```bash
# PowerShell：列出涉及文件行数，Python 应 ≤800、Vue ≤1200
Get-ChildItem webui/*.py, scripts/boss/*.py, scripts/boss_cdp_raw.py |
  ForEach-Object { "{0}`t{1}" -f (Get-Content $_.FullName | Measure-Object -Line).Lines, $_.FullName }
(Get-Content webui/src/views/DiscoveryView.vue | Measure-Object -Line).Lines
```

## 2. 后端测试

```bash
uv run python -m unittest tests.test_repo_hygiene   # 卫生
# 全量入口以「基线」步骤实测确认的命令为准
```

预期：全绿，且 `git status` 显示 tests/ 无任何改动。

## 3. 前端测试与构建（B8 或涉及前端时）

```bash
cd webui && npm run test && npm run build
```

预期：452 用例全绿，构建成功。

## 4. 卫生检查

```bash
uv run python -m unittest tests.test_repo_hygiene
git diff --check && git status
```

## 5. 冒烟（每批可选，B6/B8 后必做）

启动 webui，跑一次小规模抓取 + AI 筛选流程，确认启动、抓取、筛选、历史查看与拆分前无差异。

## 6. 提交

单批全绿后：`refactor(scope): <批次描述>`，不 push。
