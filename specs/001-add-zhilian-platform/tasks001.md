# Tasks 001：平台内核与 BOSS 兼容基线

## 新会话启动提示词

```text
请在当前仓库根目录执行 specs\001-add-zhilian-platform\tasks001.md。先读取仓库根目录 AGENTS.md，以及该功能目录中的 spec.md、plan.md、research.md、data-model.md、quickstart.md 和 contracts\ 全部文件；正式实施前输出【已查阅】。

本会话只负责 tasks001.md，不要提前执行 tasks002.md 至 tasks008.md。先现场运行启动门禁和现有 BOSS 基线，不要相信旧会话的完成声明。按任务文件逐项实施、测试和勾选；每到节点门禁都重新核验真实代码与测试，缺少条件时停止对应节点并明确报告，禁止猜接口、削弱测试或扩大范围。完成后按 AGENTS.md 运行验证、仓库卫生检查、检查 git diff，只提交本任务改动，不 push，不自动开始后继任务。
```

## 给独立执行 AI 的指令

本文件设计为可在全新会话中单独执行。开始前必须读取仓库根目录 `AGENTS.md`，再读取本功能目录的 `spec.md`、`plan.md`、`research.md`、`data-model.md`、`quickstart.md` 和 `contracts/` 全部文件。冻结规格高于本任务中的概括；发现冲突时停止实施并报告，不得自行改写规格。

本任务是全部后继任务的基座。只建立平台公共合同并保持 BOSS 行为，不实现智联真实抓取、不执行 migration 27、不改平台工作台 UI。

## 启动门禁

1. 确认 `tasks002.md` 至 `tasks008.md` 尚未留下未提交的实现改动；若工作区已有改动，先识别所有权，不得覆盖。
2. 运行当前 BOSS 聚焦测试并记录基线：`uv run python -m unittest tests.test_execution_config tests.test_webui_core tests.test_chrome_setup tests.test_healthy_pipeline`。
3. 若基线失败，区分既有失败与本任务相关失败；无法形成可信基线时停止，不得通过削弱测试继续。

## 允许与禁止范围

允许修改：`webui/platforms.py`、`webui/source.py`、`webui/execution_config.py`、`webui/core.py`、`webui/workbench.py`、`scripts/boss_cdp_raw.py` 及直接对应测试。确需在 `webui/app.py` 暴露基础注册接口时只能做最小接线。

禁止修改：数据库 migration、智联真实 scraper、Vue 工作台、调优执行逻辑。禁止复制 `get_jobs` 源码，禁止改变 BOSS 现有列表搜索语义，禁止让列表搜索接收 AI filters。

## 节点门禁 A：建立公共类型前

先用 `rg` 定位现有 source、scope digest、城市、URL 和 CDP 端口实现。只有确认当前符号与调用方后才允许编辑。若预计源码落点与现状不同，以现状为证据调整文件位置，并在提交说明中记录，不得虚构不存在的模块。

- [ ] T001 盘点 BOSS source、scope、城市、URL、错误码和 CDP 端口的定义及调用位置，记录到本任务实施日志，不修改业务代码
- [ ] T002 为平台键、启用状态、显示名、筛选 schema、城市解析、URL allowlist 和运行配置建立唯一注册边界 `webui/platforms.py`
- [ ] T003 为 `boss` 注册现有字段集合、城市映射、URL 规则、默认端口和兼容行为，并用 `tests/test_platforms.py` 固定 BOSS 基线
- [ ] T004 在 `webui/source.py` 定义平台无关 `JobSource`、`SourceOutcome`、`preflight/fetch_list/fetch_detail/fetch_details_batch` 合同及安全失败码

## 节点门禁 B：改造 BOSS adapter 前

必须确认 `JobSource` 测试替身已能表达 `platform`、显式 `cdp_port`、空结果、失败和暂停。若协议仍不能无歧义表达合同中的错误矩阵，先修正公共合同；不得在 BOSS adapter 内引入临时特例。

- [ ] T005 [P] 为 `FakeJobSource` 和 SourceOutcome 合同添加平台、显式端口及安全结果测试到 `tests/test_source.py`
- [ ] T006 将现有 BOSS source 包装为 `boss` adapter，并保证列表、单详情、批详情均从冻结运行配置接收显式 CDP 端口 `webui/source.py`
- [ ] T007 修改 `scripts/boss_cdp_raw.py` 的调用边界以显式接受端口，同时保持现有 CLI/业务兼容并补 `tests/test_chrome_setup.py` 回归
- [ ] T008 在 `webui/execution_config.py` 中让 platform 进入 scope/task digest 的规范输入，禁止省略平台的新版调用与跨平台城市码复用
- [ ] T009 在 `webui/core.py` 和 `webui/workbench.py` 提供平台校验、schema 投影、URL 规范化和岗位身份校验公共函数，不实现数据库 upsert

## 节点门禁 C：兼容入口与搜索分层

改接口前逐项读取 `contracts/http-api.md` 的 Legacy BOSS-only 矩阵。此节点只建立公共拒绝函数和 BOSS 基线；完整逐路由接线属于 `tasks007.md`。若现有搜索请求仍依赖非空 AI filters，必须先找到真实调用链并拆分，不能简单丢弃字段导致行为静默变化。

- [ ] T010 固定搜索仅接收关键词、规范城市和页数，非空 AI filters 返回 `search_filters_not_supported`，在 `tests/test_webui_core.py` 覆盖
- [ ] T011 提供 legacy 平台参数解析与 `legacy_platform_not_supported` 零副作用拒绝助手，并为显式 boss/省略平台保留兼容 `webui/core.py`
- [ ] T012 运行 BOSS 聚焦回归并修复本任务引入的回归，不得扩大到 migration、智联或前端功能

## 完成门禁

必须同时满足：平台规则只有一个权威注册边界；BOSS adapter 符合统一协议；显式端口已贯穿；scope/task digest 含平台；搜索和 AI filters 分层；BOSS 聚焦测试通过。运行：

```powershell
uv run python -m unittest tests.test_platforms tests.test_source tests.test_execution_config tests.test_webui_core tests.test_chrome_setup tests.test_healthy_pipeline
uv run python -m unittest tests.test_repo_hygiene
```

随后检查 `git status`、`git diff`，只暂存本任务文件，使用仓库规定邮箱创建 Conventional Commit。测试失败或存在不明改动时禁止提交。

## 解锁条件

只有完成门禁全部通过且提交存在，才解锁 `tasks002.md`、`tasks003.md`、`tasks006.md`，以及 `tasks007.md` 中不依赖数据库和智联 source 的前置测试工作。后继 AI 不得只凭本文件勾选状态判断，必须现场核验代码、测试和 git 历史。
