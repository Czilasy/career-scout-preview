# Tasks 006：前端平台工作台

## 新会话启动提示词

```text
请在当前仓库根目录执行 specs\001-add-zhilian-platform\tasks006.md。先读取仓库根目录 AGENTS.md、智联功能目录全部冻结工件和 tasks001.md；进入真实 API 联调前再读取并现场核验 tasks005.md。正式实施前输出【已查阅】。

本会话只负责 tasks006.md。后端主链未完成时可以按冻结合同用 mock 实现和测试前端，但必须把 mock 阶段与真实联调阶段分开，不能用 mock 通过宣称整个任务完成。当前草稿平台、任务平台和最近结果平台必须保持独立；不得由前端猜平台、发布 schema 或用简历建议覆盖权威标签。每到异步竞态、任务恢复、真实 API 联调和视口检查节点都重新验证前置条件。必须实际检查 1440×900 与 390×844 渲染。完成全部联调后只提交本任务改动，不 push，不自动执行 tasks008。
```

## 给独立执行 AI 的指令

本任务可在后端主链尚未完成时基于冻结合同和 mock 独立开发。开始时读取根规则、全部冻结工件及 `tasks001.md`；联调阶段再读取并核验 `tasks005.md`。

## 总前置门禁

确认平台注册字段、HTTP 请求/响应、状态映射和错误码已经在 contracts 中冻结。若 `tasks001.md` 的实际后端投影与合同不一致，停止并报告，不得由前端自行发明兼容字段。

## 允许与禁止范围

允许修改：`webui/src/types.ts`、`api.ts`、`discovery.ts`、`App.vue`、`views/DiscoveryView.vue`、`components/BrowserAccountsDialog.vue`、`JobWorkspace.vue`、`TaskProgress.vue`、`styles.css` 和对应 Vitest。

禁止修改：后端 store/source/状态机、schema 权威值、智联选择器。前端不得按 URL 猜平台，不得用简历分析响应覆盖平台注册表标签。

## 节点门禁 A：状态模型

首次编辑 UI 前，确认当前草稿平台、运行/历史任务平台和最近结果平台可作为三个独立状态表示。若现有单一字段混用，先写失败测试再拆分，不能用 watcher 相互覆盖。

- [ ] T501 为平台注册、schema、城市、任务、结果、双岗位 ID 和稳定错误码建立 TypeScript 类型 `webui/src/types.ts`
- [ ] T502 在 `webui/src/discovery.ts` 拆分新任务草稿平台、任务平台和最近结果平台状态，并写恢复/切换单元测试
- [ ] T503 在 `DiscoveryView.vue` 增加 BOSS/智联分段控件，切换只作用于新任务草稿

## 节点门禁 B：schema、城市与建议

首次应用异步响应前，必须有请求序号或取消机制测试，证明旧平台响应晚到不会覆盖当前平台。首次应用简历建议前，必须按当前已加载 schema 投影。

- [ ] T504 为 BOSS/智联 schema 与城市交错响应、取消和错误归属编写 100 次竞态测试
- [ ] T505 实现按平台加载 schema/城市、丢弃陈旧响应，并保持关键词、规范城市、页数和公共筛选草稿
- [ ] T506 实现 `boss.stage` 与 `zhilian.company_nature` 独立草稿、渲染及 allowlist 提交
- [ ] T507 实现简历分析建议按当前 schema 投影，拒绝 stage/company_nature 串用且不替换权威标签
- [ ] T508 确保 execute-search 不提交 AI filters，只有 ai-screen 提交 schema 版本和当前平台筛选

## 节点门禁 C：任务恢复与结果展示

mock 阶段按合同开发。切换到真实 API 前必须现场运行 `tasks005.md` 的相关后端测试，并检查响应真实含 platform、task_input_digest、双 ID 和唯一公共状态；缺字段时停止联调，不能在前端补默认 BOSS。

- [ ] T509 恢复 queued/running/paused/interrupted 时先设置任务自身平台，再加载对应 schema、城市和筛选快照
- [ ] T510 在 TaskProgress 展示真实平台、暂停原因、阶段和可执行操作，取消/继续/结束不提交草稿平台选择
- [ ] T511 在 JobWorkspace 展示结果自身平台、经验、学历、extra、规范链接和双 ID，草稿切换不重标结果
- [ ] T512 更新 BrowserAccountsDialog：activate 只选账号草稿，open 显式选择平台，delete 展示双平台占用错误
- [ ] T513 覆盖空状态、加载中、成功、失败、暂停、partial、无 source 证据和平台禁用状态

## 节点门禁 D：真实渲染

只有组件测试和构建通过后才能做视口验收。必须使用真实渲染，不得仅凭源码判断。检查 1440×900 和 390×844 的平台切换、筛选、状态、结果和账号对话框。

- [ ] T514 修复两个视口下的重叠、横向溢出、逐字竖排、不可达操作、焦点态和可点击区域问题 `webui/src/styles.css`
- [ ] T515 运行真实后端联调，验证草稿平台、任务平台、最近结果平台互不改写

## 完成门禁

```powershell
npm --prefix webui test
npm --prefix webui run build
uv run python -m unittest tests.test_repo_hygiene
```

保存必要的测试证据但不提交本地产物或截图，检查 diff 后独立提交。后端未完成时只能提交 mock 可验证部分，并明确不得解锁最终集成；完成真实 API 联调和视口检查后才算本任务完整通过。

## 解锁条件

完整通过后解锁 `tasks008.md` 的前端集成验收。若只完成 mock 阶段，必须由后续会话继续本文件 T509-T515，不能把 mock 测试当作完整完成。
