# Tasks: 执行搜索路由与 Discovery 门面薄化（047）

## File Boundaries

- **Allowed**：`webui/exec_search_api.py`、`webui/src/views/DiscoveryView.vue`、本 Spec 新增的提取模块、对应聚焦测试、047/B096 门禁文档。
- **Forbidden**：BOSS/智联抓取树枝、B106 去重、第三平台、凭据、本次拆分之外的产品行为。
- **Line gate**：Python `<800`，Vue `<1200`；DiscoveryView 必须完成至少一个高内聚区块提取，并以薄装配证据说明后续功能不把业务逻辑加回 view。

## Ordered Tasks

- [x] T001 在现有搜索执行与 Discovery 测试边界增加 characterization 断言；先观察行数目标失败，再开始生产代码提取。
- [x] T002 [P] 将 `exec_search_api.py` 的纯职责提取到新模块，保留路由注册入口和 monkeypatch/响应契约。
- [x] T003 [P] 将 DiscoveryView 的高内聚模板/事件区块提取到独立组件或 composable，保持 DOM、事件、文案和键盘契约。
- [x] T004 运行聚焦兼容测试和直接相邻回归，记录测试等级、数量、数据类型和未测范围。
- [x] T005 核对行数、依赖方向、禁区、`git diff --check`，把证据同步到 B096 T001–T003。

## Definition of Done

- 既有搜索执行 API 与 Discovery 测试通过；无行为变更证据。
- 行数达到门禁；新文件职责单一且未引入外部依赖。
- B096 T001–T003 记录为可继续，随后由 B096 tasks 继续，不在 047 内实现 B096 产品代码。
