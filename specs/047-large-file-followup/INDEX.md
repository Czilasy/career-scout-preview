# 047 Spec 索引

本目录记录 047「执行搜索路由与 Discovery 门面薄化」的内部拆分与兼容验证。

## 当前版本

- 当前版本：`v1`
- 默认读取：`v1/`
- 当前阶段：v1 拆分与兼容验证已完成；后续 B096 仅保留薄装配接线。
- 业务主体：既有搜索执行 HTTP 路由与 Discovery 工作台门面；本 Spec 不新增用户行为。

## 范围

- `webui/exec_search_api.py`：保持既有路由与响应契约，提取内部职责，降至 Python 800 行以内。
- `webui/src/views/DiscoveryView.vue`：保持 DOM、事件和交互契约，提取高内聚模板区块，使后续功能只做薄装配。
- 只记录结构拆分和兼容证据；B096 的流程、平台和数据行为仍由 `specs/046-parallel-platform-flow/` 负责。

后续同主体迭代新建 `v2/`，历史版本只读。
