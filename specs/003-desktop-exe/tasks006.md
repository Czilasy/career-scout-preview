# Task 006：PyInstaller 打包配置与构建脚本

**所属 Wave**：3（串行） | **硬前置**：Task 004、005 完成 | **用户故事**：EXE6（构建可复现）

## 必读文件

- 仓库根 `AGENTS.md`
- `specs/003-desktop-exe/spec.md`（成功标准 SC-009/SC-010）、`research.md`（PyInstaller/WebView2 事实）
- Task 005 产出的 `packaging/desktop.py`

## 写入范围（互斥）

`packaging/career_scout.spec`、`packaging/build_exe.ps1`、`packaging/README.md`（均新增）、`.gitignore`（核补）。**禁止**修改 `webui/`、`scripts/`、`packaging/desktop.py`。

## 原子清单

- [ ] T041 [P] 记录 `pyproject.toml` 当前版本、`.gitignore` 现状（`build/`、`dist/`、`.release/` 已忽略确认）、`webui/package.json` 构建脚本名（只读）
- [ ] T042 编写 `packaging/career_scout.spec`：
  - entry：`packaging/desktop.py`（`Analysis` 的 pathex 含项目根，确保 `scripts`/`webui` 可导入）
  - onefile + windowed（无控制台）；EXE 名 `CareerScout`；版本从 `pyproject.toml` 读取注入（spec 内实现或构建脚本注入）
  - `datas`：`webui/dist` → `webui/dist`、`data/city_codes.json` → `data/`、`data/zhilian_city_codes.json` → `data/`（与 `data/` 目录实有文件对齐）
  - `hiddenimports`：先按缺什么补什么的策略收敛（构建验证时报缺的模块逐项加），必须覆盖 flask、keyring（Windows 后端）、webview 相关
- [ ] T043 编写 `packaging/build_exe.ps1`：
  - 前置校验：`webui/dist/index.html` 存在（否则先 `npm ci` + `npm run build`，失败非零退出）
  - `uv run pyinstaller packaging/career_scout.spec --noconfirm`
  - 产物移动/重命名为 `.release/CareerScout-v{version}.exe`（`dist/` 是 PyInstaller 默认输出，避免与 `webui/dist` 混淆）
  - 任一失败步骤非零退出；成功输出产物绝对路径
- [ ] T044 编写 `packaging/README.md`：构建前置（Python/Node/uv）、步骤（ps1 执行方式）、产物位置、常见排错（杀软误报、缺依赖、WebView2 缺失）、**Release 发布流程**（本地构建 → 创建 GitHub Release → 上传 EXE + SHA256 → 发布说明模板要点）
- [ ] T045 核补 `.gitignore`：确认打包中间产物（如 `packaging/build-*` 或 PyInstaller work 目录，按实际产生位置）不入库；`build/`、`dist/`、`.release/` 已忽略无需重复
- [ ] T046 运行 `uv run python -m unittest tests.test_repo_hygiene` + `git status` 核实产物零入库，提交：仅 `packaging/*`（desktop.py 除外，已由 Task 005 提交）、`.gitignore`，信息 `build: add exe packaging scripts`

## 完成定义

spec 与脚本语法自检通过（脚本可 dry-run 或至少 parse）；卫生测试通过；`git status` 无构建产物；文档覆盖构建与发布两条流程。

## 提交纪律

只暂存本包文件；commit email `czyooutzilas@gmail.com`；提交前 `git diff --check` 与 `git status --short`。