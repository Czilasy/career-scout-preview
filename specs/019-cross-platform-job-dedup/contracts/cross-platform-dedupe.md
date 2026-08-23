# Contract: 跨平台去重数据契约（019）

**范围**: 本功能不新增/不修改任何 HTTP 端点。本契约为**数据载荷契约**：跨平台剔除记录在既有响应载荷中的结构，以及前端映射规则。后端权威定义见 `webui/cross_platform_dedupe.py`，前端消费见 `DiscoveryView.vue` / `JobWorkspace.vue`。

## 1. 剔除记录（后跑平台轮次的 dropped 行）

来源：`GET /api/latest-pipeline-result`（及历史轮端点）返回的 `result.dropped[]` 中，跨平台重复剔除行具有以下可区分特征：

```json
{
  "platform": "zhilian",
  "platform_job_id": "<当前平台岗位ID>",
  "title": "...", "company": "...", "salary": "...", "location": "...",
  "reason": "跨平台重复：已在 BOSS 保留",
  "extra": {
    "cross_platform_dup_of": {
      "platform": "boss",
      "platform_job_id": "<对端平台岗位ID>",
      "source_url": "https://www.zhipin.com/job_detail/....html",
      "finished_at": "2026-08-23T10:00:00+08:00"
    }
  }
}
```

**不变式**:

- `extra.cross_platform_dup_of.platform` ≠ 该行 `platform`（只在跨平台之间生效）。
- `reason` 文本以「跨平台重复」开头，含对端平台展示名（BOSS / 智联）。
- `finished_at` 为对端**最近一个包含该岗位的可见轮**的定稿时间（判定源覆盖对端近 30 天全部可见轮，不限于最新轮）。
- 对端平台近 30 天无可见轮、或全部参与轮被画像过滤/全剔除行跳过时，不产生本结构记录。
- 判定行（verdict 落库）只有 `verdict="dropped"` 与 reason 文本；`extra` 结构仅存在于轮次结果载荷（dropped 行 `extra_json`），由输入组装点重算恢复。

## 2. 前端映射规则（合并视图 · 重复簇成组）

`DiscoveryView.fetchMergedLatestResult` 合并两平台结果后：

1. 遍历 `dropped[]` 中含 `extra.cross_platform_dup_of` 的行（簇成员：本平台副本）；
2. 以 `(dup_of.platform, dup_of.platform_job_id)` 为键反查合并后 `jobs[]`（簇主：对端保留条目）；
3. 命中 → 在簇主上挂运行时簇数据 `_also_on_copies: Array<{platform, salary, source_url, platform_job_id}>`（本平台副本的字段取自 dropped 行自身）；未命中 → 跳过（对端条目随轮次顶替不可见，退化为剔除台账条目，不报错）；
4. `JobWorkspace` 列表行对带簇数据的岗位渲染「双平台在招」徽标；详情面板渲染成组区，并排展示各平台副本（平台名、薪资、岗位链接），薪资如实展示各自写法。

**不变式**: 簇数据为运行时派生，不持久化、不回写任何请求载荷；旧轮次（无 `cross_platform_dup_of`）不产生簇。

## 3. 提交开关（既有端点的请求字段扩展）

`POST /api/ai-screen` 请求体新增可选布尔字段 `cross_platform_dedupe`：

- 缺省或 `true`：启用跨平台去重（默认行为）。
- `false`：本次筛选不做跨平台剔除（行为与现状一致）。
- 取值随该 run 的执行参数冻结：断点续跑、服务重启恢复均沿用提交时的取值；一键自动接续（`auto_screen_fields` 快照）随快照携带。
- 前端开关位于一键筛选对话框（`OneClickScreenDialog.vue`），默认开，选择本地记忆（localStorage）。

## 4. 可见性契约（报数与台账）

- 筛选进度事件（SSE/任务状态流）：去重发生时含明确数量文案「N 条中 M 条跨平台重复，跳过 AI 筛选」；M=0 时不产生额外消息。
- 完成文案与结果统计：「抓取 X / 跨平台重复 M / 实际筛选 X−M」三数互洽，可对账。
- 任务事件：每次筛选的去重判定追加一条 `cross_platform_dedup` 事件，载荷含被剔岗位清单（job_id、标题）与各自 `dup_of` 指向（对端平台、岗位 ID、链接、轮定稿时间）；任务详情事件流可查。

## 5. 计数契约（口径不变，语义收紧）

- `total_scraped`：含跨平台重复（抓到的都算）。
- `total_dropped`：含跨平台剔除数。
- 合并视图对两平台计数求和的既有逻辑不变。
