# Phase 0 Research: 偶发失败重试与失败展示优化

本文件记录关键设计决策、理由与被否决的替代方案。所有决策对齐已冻结 spec.md。

## 决策 1：重试额度与既有恢复重试合并

- **Decision**: 每个组合维护单一“已用重试”标志；既有“失联自动重启后重试”“登录复核通过后重试”与新增“偶发重试”共享同一额度；同一组合合计最多两次抓取尝试（首次 + 一次重试）。
- **Rationale**: 规格 SC-002 明确“尝试次数不超过 2 次”；现有两个小重试若独立计数，叠加后会变成最多三次尝试。
- **Alternatives considered**: ① 各通道独立计数——会被 SC-002 判失败；② 重构整个恢复机制为统一状态机——超出“简单”边界，违反规格 FR-013 的非目标。

## 决策 2：重试起点（从断点续抓）

- **Decision**: 复用组合内实时断点（页级完成回调维护的“恢复起始页”与“已抓岗位快照”内存映射）+ 既有 `plan_adapter.apply_resume_fields`，重试从该组合已完成页的下一页继续。
- **Rationale**: 现有失联重试分支已验证该模式；无需读数据库、不会重复抓取已保存内容（规格 FR-002）。
- **Alternatives considered**: ① 重试时读 DB 页级表——多一次 IO 且可能与内存断点不一致；② 整组重抓——浪费配额与时间，且违反断点续抓既有行为。

## 决策 3：可重试失败的判定集合（白名单）

- **Decision**: 新建 `webui/pipeline_exec_retry.py`，定义偶发可重试白名单：`source_timeout`、`source_unreachable`、`source_invalid_output`、`source_unknown_error`、`source_result_write_failed`。登录失效类在更早的既有分支被接走；**浏览器连接中断类（`source_cdp_unavailable`）由更早的既有“失联自动重启后重试”分支优先接走（同样消耗唯一重试额度），故不进白名单**；验证码、限流、封禁、请求上限、输入漂移、岗位不存在等不重试。
- **Rationale**: 规格 FR-001 列出“超时、网络波动、浏览器连接中断、页面临时异常”，加兜底未知与本地写入失败；浏览器连接中断走既有失联通道（先恢复浏览器再重试），行为结果与规格一致（同为一组一次重试）。白名单比黑名单安全（新失败码默认不重试，避免意外放大请求）。
- **Alternatives considered**: 黑名单（除硬阻断外全部重试）——新码或未识别码会自动获得重试，风控风险不可控。

## 决策 4：重试的白箱留痕方式

- **Decision**: 复用 `ScrapeEvidence.record_fact()` 写 `retry_scheduled` 事件（severity=info、required_evidence=False，attempt=1）；最终跳过沿用既有 `evidence.failed()`，重试成功沿用 `evidence.completed()`。`webui/whitebox_evidence.py` 不做修改。
- **Rationale**: “全部复用现有零件”；`record_fact` 是既有公开入口；info 类事件不参与结论归约（与既有 `page_completed` 等一致）。
- **Alternatives considered**: ① 在白箱适配器新增专用方法——非必要改动；② 新增数据表或字段——规格明令禁止。

## 决策 5：列表抓取原因正名（连不上浏览器）

- **Decision**: 在 `webui/source_zhilian_runtime_adapter.py` 新增列表侧补映射：`{"cdp_unavailable": "source_cdp_unavailable"}` 与 `build_zhilian_list_signal_map()`；`webui/source_zhilian_cdp.py` 的列表映射改为经它构造（净增 ≤3 行）。
- **Rationale**: 与详情侧既有模式（additions + build 函数）完全同构；原因归一后自动获得注册表名称“连不上调试浏览器”，并进入“浏览器失联”自动恢复通道（规格 FR-010/FR-011）。
- **Alternatives considered**: ① 直接改 `source_zhilian_cdp.py` 中的字典字面量——该文件在预警线以上且破坏“新增 signal 走 adapter”的既有分工；② 修改 `scripts/zhilian/search.py` 的返回值——属于抓取逻辑本身，规格禁止。

## 决策 6：失败展示与悬停浮窗

- **Decision**: 前端 `TaskProgress.vue` 删除 `combo_issues` 成排列表渲染；失败数量处实现轻量 hover 浮窗，逐条显示 `组合名：简单原因`（最多 5 条，超出显示“另有 N 条”）；数据沿用后端既有 `combo_issues` 字段，后端不改。
- **Rationale**: 规格 US2 + FR-008/FR-009/FR-015；复用既有数据；不引入第三方组件。
- **Alternatives considered**: ① 后端新增接口——不必要；② 原生 `title` 属性——无样式控制、多条内容体验差。

## 决策 7：收尾后计数口径

- **Decision**: `scrapeCountState` 兜底分支改用后端真实数字（已完成取 `success_count`，跳过取 `fail_count`，未开始 = 总数 − 已完成 − 跳过）；运行中维持既有派生逻辑。
- **Rationale**: 后端在抓取语义下已算对（`is_scrape` 分支）；问题仅在于前端兜底分支写死“0 完成、全部未开始”（规格 FR-012）。
- **Alternatives considered**: 修改后端计数——后端无错，改动面反而扩大。
