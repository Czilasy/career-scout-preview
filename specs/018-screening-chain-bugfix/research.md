# Research: 筛选链路三处 Bug 修复（018）

**Date**: 2026-08-22 | **Spec**: [spec.md](spec.md)

## R1 事故机理实证（live 库只读核查，非重新调查）

对 `~/.career-scout/webui/webui.db`（env=live）只读核查 scrape_task_id=5d11875046314a109ec4786b265db909 的链路，与冻结核叙述一致并补齐关键数字：

| run | 状态 | screening_results | ai_rough checkpoint | ai_fine checkpoint |
|---|---|---|---|---|
| 03fb82e1 | failed(resumed) | 277 条（112 dropped + 155 kept + 10 精筛 not_match） | 277 keys | 10 keys |
| 0f0baa1b | failed(resumed) | 40 条（40 not_match 精筛） | **277 keys**（继承写回） | 50 keys |
| 94e2c440 | failed(internal_error) | 0 条 | **40 keys**（旧代码塌缩后写回） | 无 |
| 828f8807 | done（result_snapshot 幽灵轮） | — | — | — |

- 决策：机理确认。旧条件 `verdict in (_FINE_VERDICTS | {"kept"})` 以"最近一条 run 自身判定"为准，94e2c440 续跑 0f0baa1b 时 277 断点里只有 40 条精筛判定可见 → 幸存者塌缩 40；塌缩后的 40 又被写进 94e2c440 的 ai_rough 断点。
- 理由：数字闭环（277→165→40）全部对上。

## R2 判定来源必须用合并链（对冻结核代码片段的必要校正）

- 决策：主修条件 `_resume_verdicts.get(jid, "") != "dropped"` 中的 `_resume_verdicts` 必须取自**同源链合并后的 resume_verdicts**（app.py:3102 已在作用域内），不能再按现状从 `store.load_screening_verdicts(resume_from_run_id)` 只读最近一条 run。
- 理由：live 库实证 94e2c440 名下 0 判定。若只读 run 自身，112 个 dropped 不可见 → 条件反转后 277 个断点岗位全部保留，幸存者=277 而非 165，且与 `_resume_dropped_from_verdicts`（用合并版）产出的 dropped 列表重叠冲突。用合并版则 165/112 精确复现。
- 替代方案（否决）：只做条件反转不改判定来源——会在真实事故链上产出 277 幸存者 + 112 dropped 的自相矛盾结果。

## R3 断点岗位重筛与 dropped 恢复的去重护栏

- 决策：app.py:3240-3242 的 `_resume_dropped_from_verdicts` 合并进 dropped 列表时，跳过已在 kept_ids 里的岗位（`if jid not in kept_ids`）。
- 理由：塌缩断点场景（如 94e2c440 的 40 keys）下，237 个岗位会重新粗筛；若本轮 AI 判 kept 而链上旧判定是 dropped，岗位会同时出现在幸存者与 dropped 两个列表。"新的覆盖旧的"是冻结核明确的合并语义，重筛结果比链上旧判定新，应以此消歧。
- 替代方案（否决）：不加护栏——出现同一岗位双列表计入，计数与页面展示自相矛盾。

## R4 store 查询扩展形态

- 决策：`latest_screen_runs_for_source(source_task_id, statuses=None)` 增加 `statuses=None` 分支：不筛状态、排除 result_snapshot、按 `created_at ASC, rowid ASC` 返回全部 run；既有调用（传 statuses）行为不变。不新增方法、不加其它旋钮。
- 理由：合并需要"全部状态、时间升序"；一次查询拿全链再在编排层做条件/画像校验（与 find_resumable_screen_run 同款比对：frozen_filters 相等、profile_summary 字符串相等、_same_facts 画像事实相等）。
- 替代方案（否决）：per_status_limit 等更多参数——用不上，徒增接口面积。

## R5 收尾换序的落点

- 决策：新顺序 = job_events → 计数(_write_run, current_stage="done") → emit(done) → finalize 校验（不合法抛 RuntimeError，此刻库里无历史轮）→ save_finished_round(status="done") → history_snapshot 事件 → _prune_history_best_effort → 内存置 done 与清理。`final_db_status` 仅在该块内使用，无后续引用，可安全随块搬移。
- 理由：017 的"先写事件再提交终态"契约保留（emit(done) 仍在任何终态写入之前）；RuntimeError 走既有 internal_error 失败路径，任务 failed 且零历史轮。
- 已核对既有测试：tests/test_result_rounds.py 只测 save_finished_round 单元行为，不涉及 app.py 收尾顺序；tests/test_webui_app.py 中 017 用例断言"失败/暂停零历史轮"，与换序方向一致；若有断言成功路径内部顺序的用例，实现时同步修正。

## R6 粗筛同款解析确认

- 决策：ai.py `_process_batch`（约 1553 行）`dropped_list = data.get("dropped", [])` 与精筛同款无守卫，加同款守卫。
- 理由：冻结核要求"存在同样写法才加同样守卫"——确认存在。

## R7 merge 的闸门保留

- 决策：`load_resume_verdicts_with_fallback` 的第一段（run 自身判定 + ai_rough 断点数闸门）保持不动；仅替换 128-146 行的快照回退段。
- 理由：闸门语义（判定数 ≥ 断点数 → 不需要回退）依然成立；真实事故链（run 自身 0/40 判定 < 断点 40/277）均会进入合并分支。
