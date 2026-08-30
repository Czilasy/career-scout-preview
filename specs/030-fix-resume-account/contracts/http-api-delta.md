# HTTP API 契约增量: 续跑账号身份修复

**Date**: 2026-08-30 | **Spec**: [spec.md](./spec.md)

本批次不改动任何请求格式与既有成功响应结构。以下为行为级契约增量。

## POST /api/task/continue/<run_id>

**请求**：不变（`target_account` 可选字段语义不变：显式指定时始终优先并走既有校验链）。

**响应**：不变（成功/失败结构、状态码均与现状一致）。

**行为增量**：
- 自动换号从"当前全局账号 ≠ 冻结账号即换"收紧为双门槛：
  1. 当前全局账号 ≠ 创建时快照 `active_account_at_freeze`（快照缺失 → 不自动换）；
  2. `run.error_code` 不属于 AI 类阻断（ai_rate_limited / ai_quota_exhausted / ai_key_invalid / ai_network_error）。
- 双门槛命中并完成换号时：任务事件新增 `account_switch` 记录；续跑启动后任务日志含一行中文换号说明。
- 未命中双门槛：执行参数中的账号/端口/资料目录字段不被改写（现状为可能被改写，本契约为收紧性变化）。

## POST /api/job-detail

**新增拒绝分支**：

- 条件：任一任务处于 running / queued 状态。
- 响应：`409`，JSON 含 `"ok": false` 与中文提示（口径同其它任务入口的并发拒绝，"当前已有任务在运行…"风格）。
- 时序：门禁先于身份继承与浏览器激活执行，被拒请求不产生任何浏览器目录/全局状态副作用。
- 其余行为（200 成功结构、502 抓取失败、409 run_identity_conflict 等）不变。

## POST /api/execute-search、POST /api/screen（筛选提交）、POST /api/pipeline/recrawl（含单岗位重抓）

**响应**：不变。

**行为增量**：创建任务时执行参数新增 `active_account_at_freeze` 快照键（对客户端不可见、不要求回传）。

## GET /api/runs/<run_id>/diagnostics

**行为增量**：事件列表可能新增 `account_switch` 种类条目（结构沿用既有事件条目格式：kind + payload + 时间戳）。
