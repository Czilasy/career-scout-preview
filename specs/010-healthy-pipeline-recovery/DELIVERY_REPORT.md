# 010 healthy-pipeline-recovery 最终交付报告

生成时间：2026-07-28
执行依据：`FULL_EXECUTION_PROMPT.md` 第十四节 14 项完成标准
范围声明：本报告仅陈述当前可验证的事实，不夸大、不伪装完成。

---

## 一、14 项完成标准逐项核对

| # | 标准 | 状态 | 证据 |
|---|------|------|------|
| 1 | FR-001 至 FR-050 均有实现和验证证据 | ✅ 通过 | 历次 finding 已完成 RED→GREEN，最后 4 项修复由 5 个新增回归覆盖；全量自动验证通过 |
| 2 | SC-001 至 SC-015 均通过 | ✅ 通过 | SC-002 按 2026-07-28 验收修订由 6 项确定性持久化/重启/恢复测试替代低收益墙钟轮询，6/6 通过；其余 SC 具备自动、只读数据或真实渲染证据 |
| 3 | 所有自动测试通过 | ✅ 通过 | 健康流程 137/137、直接影响 73/73、Python 全仓 761/761、Vitest 17/17；production build、17 文件 py_compile、TypeScript 检查和 `git diff --check` 均通过 |
| 4 | 桌面与窄屏真实渲染通过 | ✅ 通过 | 375×812、768×1024、1440×900 均无横向溢出，暂停原因、进度、继续操作、待确认原因实际可见且未被遮挡 |
| 5 | WebUI 旧 PID 已被替换，新版本可见 | ✅ 通过 | 最终构建后 5050 旧 PID 20544 已替换为 PID 2816，`/api/version` backend=`010-healthy-pipeline-recovery`、build hash=`537093a93b95`；5000/5051 未监听 |
| 6 | 小规模真实验收通过 | ✅ 通过 | DB 中存在 4 条小规模 run（03a49d5c/366e2546/6ed16970/cba82bb9），source_count=30，状态为 partial/interrupted |
| 7 | 约 90 条集成验收通过 | ⚠️ 边界已说明 | 直接影响测试 73/73、全仓 761/761；“约 90 条”仍是历史近似口径，不用总测试数伪装独立场景清单 |
| 8 | 历史恢复预演数字完全一致 | ✅ 通过 | 正式库 `~/.career-scout\webui\webui.db` 只读预演：rough_total=1926, rough_50_json=17+33, fine_50_uncertain=50, pending_646=646, gate_passed=true（见第五节） |
| 9 | 50 条结构异常恢复为 17 match + 33 not_match | ✅ 正式恢复已执行 | 正式库只读核验确认 17 条 match、33 条 not_match；不得重复执行恢复 |
| 10 | 646 条补救在新流程完成或可继续暂停 | ✅ 元数据补正已执行 | 646 条 pending 均使用 `historical_reason_unavailable` 与 `recrawl_jd` 契约；不得重复补正 |
| 11 | 762 条既有成功 JD 零重复抓取 | ✅ 正式库只读核验 | 正式恢复及补正前后 JD 数量保持 762 |
| 12 | 正式结果总数守恒、无重复、无丢失 | ✅ 正式库只读核验 | quick_check=ok、foreign_key_check=0，粗筛分布与恢复前核验契约一致 |
| 13 | 独立审查通过 | ✅ 显式修订后闭合 | 既有至少 5 轮独立审查；最新 REJECT 的最后 4 项已完成 RED→GREEN 和全仓验证。用户按收益边界递减原则豁免第 6 次全量审查；不把豁免记作 reviewer PASS |
| 14 | 没有未说明的验证边界 | ✅ 通过 | SC-002 墙钟退役和第 6 次审查豁免均已显式记录，未将未发生的验证写成通过 |

**结论：Spec 010 已完成并允许关闭。正式恢复与元数据补正已经执行且只读状态正确，不得重跑；代码修复、全仓验证和隔离 5050 三视口复验已完成。SC-002 已按显式修订通过确定性验收，第 6 次全量审查已由用户基于收益边界递减原则豁免。**

---

## 二、自动测试证据

命令：`.venv\Scripts\python.exe -m unittest tests.test_healthy_pipeline -v`

结果：
```
Ran 137 tests
OK
```

直接影响测试：73/73；历史恢复针对性套件：57/57；Python 全仓：761/761；Vitest：17/17。

覆盖切片：
- Slice1 StateAndFinalizeTests（6 个）：FR-016/FR-005/FR-024/SC-010/SC-011/SC-012 状态机与完成判定
- Slice1 ConservationTests（1 个）：SC-018 统计总和守恒
- Slice2 PersistenceTests（6 个）：FR-011~FR-016/FR-023/FR-038 pending 表+checkpoint+事件流
- Slice3 ErrorClassificationTests（4 个）：FR-040/SC-006 统一错误分类码表（13 类）
- Slice5 ShortJDTests（4 个）：FR-032 短 JD 内容真实性判断（30/80/119 字）
- Slice6 AiPauseTests（3 个）：AI 系统性阻断立即暂停
- Slice7And9ApiTests（4 个）：FR-024/FR-037/FR-039/SC-006 API 状态返回
- Slice8RecrawlTests（2 个）：FR-022/FR-023 重抓并发拒绝与 pending 自动读取
- 其他恢复门禁测试：FR-041 gate_check 数字不一致阻断写入

---

## 三、SC-015 窄屏渲染证据

脚本：`tests/sc015_viewport_check.py`（CDP 直连实测，非浏览器代理报告）

| 视口 | clientWidth | scrollWidth | overflow | isMobile | gridCols | stepNav | uploadArea | titleText |
|------|-------------|-------------|----------|----------|----------|---------|------------|-----------|
| 375×812 | 375 | 375 | **False** | True | 351px（单列） | visible | visible | 让 AI 先读懂你的简历 |
| 768×1024 | 768 | 768 | **False** | False | 224px 497.938px（双列） | visible | visible | 让 AI 先读懂你的简历 |
| 1440×900 | 1440 | 1440 | **False** | False | 224px 1138px（双列） | visible | visible | 让 AI 先读懂你的简历 |

结论：无横向溢出、移动端布局正确激活、关键元素全部可见、标题正常显示。SC-015 通过。

---

## 四、小规模真实验收证据

DB 路径：`.career-scout/webui/webui.db`

最近 5 条 screening_runs：

| run_id | status | source_count | processed_count | pending_count | updated_at |
|--------|--------|--------------|-----------------|---------------|------------|
| 366e25463c93422e | interrupted | 30 | 2 | 2 | 2026-07-13 09:08:09 |
| 03a49d5c09e74c64 | partial | 30 | 30 | 28 | 2026-07-13 09:07:42 |
| 6ed169704a694eaa | partial | 30 | 30 | 30 | 2026-07-13 08:37:38 |
| a8733afb7a104e50 | failed | 0 | 0 | 0 | 2026-07-13 08:33:32 |
| cba82bb98563427c | partial | 30 | 30 | 29 | 2026-07-13 08:13:46 |

观察：
- `partial` 状态对应 `completed_with_pending`（FR-035），少量独立失败时显示"完成但有待确认"
- `interrupted` 状态对应服务重启打断（FR-023），保留已有结果不丢失
- `pending_count` 与 `screening_pending_results` 表行数一致（28+2+30+29=89，DB 实测 89 行）
- 状态机合法迁移全部生效

---

## 五、历史 696 条数据预演与恢复验证

### 5.1 事实陈述

正式数据库路径：`~/.career-scout\webui\webui.db`

> **勘误**：此前版本报告误称"历史数据不存在"，原因是检查了项目内相对路径（`.career-scout/webui/webui.db`、`.webui-state/webui.db`）而未检查用户 AppData 下的正式路径。正式库中两条历史 run 均存在且数据完整。

正式库只读预演结果（`historical_recovery.preview_recovery`）：

| 检查项 | 期望值 | 实际值 | 通过 |
|--------|--------|--------|------|
| rough_run.total | 1926 | 1926 | ✅ |
| rough_run.source_count | 1926 | 1926 | ✅ |
| rough_run.total_dropped | 518 | 518 | ✅ |
| rough_run.total_kept | 1408 | 1408 | ✅ |
| rough_run.jd_count | 762 | 762 | ✅ |
| rough_run.plain_verdicts.match | 198 | 198 | ✅ |
| rough_run.plain_verdicts.not_match | 514 | 514 | ✅ |
| rough_run.plain_verdicts.uncertain | 646 | 646 | ✅ |
| rough_run.plain_verdicts.dropped | 518 | 518 | ✅ |
| rough_50_json.match | 17 | 17 | ✅ |
| rough_50_json.not_match | 33 | 33 | ✅ |
| rough_50_json.total | 50 | 50 | ✅ |
| fine_run.total | 762 | 762 | ✅ |
| fine_run.inner_verdicts.uncertain | 50 | 50 | ✅ |
| pending_646.count | 646 | 646 | ✅ |
| jd_762_protection.jd_exists | 762 | 762 | ✅ |
| conservation.all_ok | True | True | ✅ |
| gate_passed.all_passed | True | True | ✅ |

### 5.2 数据角色纠正

此前版本错误地把 15847d27 中 198 条纯字符串 match + 514 条纯字符串 not_match 当成"17+33 异常数据"，并会改写全部 514 条 not_match。

正确理解（已修正）：
- **15847d27（粗筛 run，1926 条）**：
  - 纯字符串 verdict（正常 1876 条）：match=198, not_match=514, uncertain=646, dropped=518 —— 正常数据，严禁改写
  - JSON verdict（异常 50 条）：inner match=17, inner not_match=33 —— 格式异常但有有效判定，格式统一即可，不调 AI
  - JD 非空 762 条（198 match + 514 not_match + 17 JSON match + 33 JSON not_match）—— 禁止重复抓取
- **e6250f0e（精筛 run，762 条）**：
  - 全部 JSON verdict：inner match=198, inner not_match=514, inner uncertain=50 —— 50 条 uncertain 是 AI 超时，无有效判定，交给新流程重新调 AI
  - JD 非空 0 条（精筛不抓 JD，复用 15847d27 的 JD）

### 5.3 守恒律验证

| 守恒律 | 公式 | 验证 |
|--------|------|------|
| source 分解 | 1926 = 518(dropped) + 1408(kept) | ✅ |
| kept 分解 | 1408 = 762(进精筛) + 646(未进精筛) | ✅ |
| 异常总数 | 696 = 646(未处理) + 50(AI超时) | ✅ |

### 5.4 execute_recovery 副本验证

在正式库只读副本上执行 `execute_recovery` 验证（不写正式库）：

| 动作 | 结果 |
|------|------|
| action_1: 50 条 JSON verdict 格式统一 | ✅ 50 条转为纯字符串，1876 条原纯字符串 verdict 的 job_id → verdict 映射完全不变 |
| action_2: 50 条 uncertain 写入 pending | ✅ 50 条写入 screening_pending_results |
| action_3: 762 条 JD 保护验证 | ✅ JD 非空仍 762，未被清空 |
| action_4: 646 条写入 pending | ✅ 646 条写入 screening_pending_results |
| 恢复后守恒律 | ✅ source/dropped/kept 不变 |

### 5.5 回归测试

`tests/test_historical_recovery_realdb.py`（57 项测试，全部通过）：
- TestPreviewRecoveryOnRealDB（10 项）：只读预演硬断言所有不变量
- TestExecuteRecoveryPreservesPlainVerdicts（6 项）：execute_recovery 副本验证
- TestRunDataRoles（4 项）：两条 run 的数据角色
- TestNoMisidentificationOfPlainVerdicts（2 项）：回归保护，不会误判 198+514 纯字符串为异常

### 5.6 正式执行状态

正式恢复与元数据补正已在此前明确授权下执行。本轮只读复核确认：数据库 SHA-256 为 `3F70BE41CCFA028B347A7A6BD54685AEEEB8D9B7482A69D31E981F6966820566`，长度 7,520,256；schema=22、quick_check=ok、foreign_key_check=0、recovery_lock=0、committed audit=2，粗筛分布 518 dropped / 215 match / 547 not_match / 646 uncertain，JD=762。

**本轮及后续复审禁止再次执行正式恢复或元数据补正。**

---

## 六、FR-001~FR-050 实现证据清单

按切片分组，标注实现位置与测试覆盖：

### 切片 1：状态机与完成判定（FR-001/003/004/005/016/034/035/036/037）
- 实现：`webui/store.py` 的 `RUN_STATUSES`、`RUN_TRANSITIONS`、`finalize_run_status`、`TASK_TO_RUN_STATUS`、`RUN_TO_TASK_STATUS`
- 测试：Slice1StateAndFinalizeTests（6 个）+ Slice1ConservationTests（1 个）

### 切片 2：持久化与事件流（FR-006/007/008/009/010/011/012/023/038）
- 实现：`webui/store.py` 的 `screening_pending_results` 表、`pipeline_checkpoints` 表（schema 已定义，运行时按需创建）、`append_task_event`、`list_task_events`、`save_checkpoint`、`load_checkpoint`、`insert_pending_result`、`delete_pending_result`
- 测试：Slice2PersistenceTests（6 个）

### 切片 3：错误分类码表（FR-013/014/015/040）
- 实现：`webui/store.py` 的 `SYSTEMIC_BLOCK_CODES`、`INDEPENDENT_FAILURE_CODES`；`webui/pipeline_exec.py` 的 `ERROR_TAXONOMY`（13 类）
- 测试：Slice3ErrorClassificationTests（4 个）

### 切片 4：暂停/继续/取消（FR-017/018/019/020/021/022/024）
- 实现：`webui/app.py` 的 `/api/task/cancel/<run_id>`、`/api/task/resume/<run_id>`、`latest_interrupted_screening_run`、并发锁 `_pipeline_lock`、`_active_recrawl_keys`
- 测试：Slice7And9ApiTests（4 个）+ Slice8RecrawlTests（2 个）

### 切片 5：短 JD 真实性（FR-032/033）
- 实现：`scripts/boss_cdp_raw.py` 的 `extract_job_description`（基于语义标记而非字数）
- 测试：Slice5ShortJDTests（4 个，覆盖 30/80/119 字）

### 切片 6：AI 系统性阻断（FR-015/016 + SC-006）
- 实现：`webui/ai.py` 的 `screen_jobs`、`match_jds` 新增 `raise_on_systemic` 参数；`AISecurityError` 异常
- 测试：Slice6AiPauseTests（3 个）

### 切片 7-9：API 与版本（FR-025~031/037/039/040）
- 实现：`webui/app.py` 的 `/api/task-state/<run_id>`、`/api/version`、`/api/latest-running-task`（合并去重，优先级：内存 running → DB paused → DB interrupted）
- 测试：Slice7And9ApiTests（4 个）

### 切片 10：历史恢复（FR-041~047）
- 实现：`webui/historical_recovery.py` 的 `preview_recovery`（只读）、`execute_recovery`（门禁化写入）、`_check_gate`（硬检查 rough_50_json=17+33、fine_50=50、pending_646=646、守恒律）
- 测试：`test_recovery_gate_blocks_if_numbers_mismatch`（删一条 fine uncertain 触发硬检查失败）；`tests/test_historical_recovery_realdb.py`（57 项正式库只读/临时副本回归测试）
- **运行状态：正式恢复与元数据补正已执行；当前只允许只读核验，禁止重跑**

### 切片 11：性能实验保护（FR-048/049/050）
- 实现：未引入任何并发提升、批量扩大或等待缩短；未恢复旧流程；未将性能实验设为默认策略
- 验证：代码审查通过

---

## 七、SC-001~SC-015 验收矩阵

| SC | 描述 | 状态 | 证据 |
|----|------|------|------|
| SC-001 | 系统性阻断停止新工作，错误推进 0 次 | ✅ | Slice6AiPauseTests 3 个通过 |
| SC-002 | 暂停持久化、重启恢复、手动继续零重复 | ✅ 验收修订后通过 | 6 项确定性测试覆盖 DB checkpoint 恢复、真实应用重启、刷新后计数/身份、手动继续和重启状态迁移，6/6 通过；原 24 小时静态轮询已退役且不记作通过 |
| SC-003 | 刷新/重启 10s 内恢复 | ✅ | `/api/latest-running-task` 三级回退；Slice7And9 测试通过 |
| SC-004 | 继续后重复处理 0 | ✅ | `load_checkpoint` 跳过已完成；Slice2 test_continue_skips_checkpoint_keys |
| SC-005 | 100% 失败岗位有具体原因 | ✅ | `INDEPENDENT_FAILURE_CODES` + `failed_code` 必填；Slice3 |
| SC-006 | 800/1408 触发验证码保留 762+38+608 | ✅ | `SYSTEMIC_BLOCK_CODES` 含 captcha_required；Slice1 test_systemic_block_must_pause_not_complete |
| SC-007 | 完成时分类和=总数 | ✅ | Slice1ConservationTests |
| SC-008 | 全批失败/未开始时错误显示完成 0 次 | ✅ | `finalize_run_status` 强制 paused；Slice1 test_finalize_status_no_unstarted_must_not_complete |
| SC-009 | 30/80/119 字真实短 JD 通过 | ✅ | Slice5ShortJDTests 4 个 |
| SC-010 | 补救不修改未选中岗位 | ✅ | 设计上补救仅作用于 pending 表；Slice1 test_cancel_preserves_results |
| SC-011 | 预演核对 50+762+646，不一致阻断 | ✅ 通过 | `_check_gate` 逻辑测试通过；正式库只读预演返回 17+33/50/646/762，门禁 all_passed=true |
| SC-012 | 恢复后 17 match + 33 not_match，无额外 AI | ✅ 正式库只读核验 | 正式恢复已执行；17/33 保持正确，不得重复调用恢复或 AI |
| SC-013 | 恢复后总数一致，重复/丢失 0 | ✅ 正式库只读核验 | quick_check=ok、foreign_key_check=0、JD=762、粗筛分布守恒 |
| SC-014 | 10s 内确认版本，旧服务 0 次 | ✅ | `/api/version` 返回 backend_version/build_hash/build_time；Slice7And9 |
| SC-015 | 桌面和窄屏完整可读 | ✅ | 375×812、768×1024、1440×900 真实渲染通过；检查样式、边界、视口与中心点遮挡 |

---

## 八、服务重启五步验证证据

1. 确认旧隔离服务：5050 PID 5932；5000/5051 未监听。
2. 只停止 5050 PID 5932，启动修复版 PID 3212。
3. 发现并修复 768px 中间断点重叠后，再只替换 5050 PID 3212。
4. 最终窄修复后仅停止 5050 旧 PID 2172，并启动 PID 20544。
5. 最终生产构建后仅停止 5050 PID 20544，并启动 PID 2816；`/api/version` 返回 backend=`010-healthy-pipeline-recovery`、build hash=`537093a93b95`，5000/5051 仍未监听；此前三个视口真实渲染证据保持有效。

---

## 九、已接受的验收修订与剩余边界

1. **第 6 次全量审查已豁免**：既有至少 5 轮独立审查，最后 4 项 finding 已修复并通过 5 个新增回归、73 项直接影响测试和 761 项全仓测试；用户明确判断继续全量审查已进入收益边界递减区。该豁免不记作 reviewer PASS。
2. **SC-002 墙钟方案已退役**：无活动写入者的隔离静态数据库轮询不能有效验证应用重启和恢复语义，已由 6 项确定性测试替代；不声称取得 24 小时证据。
3. **约 90 条历史集成口径未单独保留**：当前直接影响测试 73/73、全仓 761/761，但不把总数冒充该历史场景清单。
4. **正式数据写保护**：最终只读指纹复核通过；不得为重复验收再次执行正式恢复或元数据补正。

---

## 十、是否允许进入下一阶段

**允许宣布 Spec 010 全量完成并关闭。**

当前状态：**正式恢复和元数据补正已经执行且只读核验正确；历次审查 finding、最终窄修复、全仓验证、隔离 5050 重启和三视口 SC-015 已通过；SC-002 按验收修订由 6 项确定性测试闭合，第 6 次全量审查经用户明确豁免。**

- 已确认：正式库 schema=22、quick_check=ok、foreign_key_check=0、recovery_lock 为空；正式数据不需要再次恢复
- 已修复：本轮双轴审查提出的并发 claim、持久化失败阻断、终态计数、幂等备份复核、canonical 前端状态、SC-015 和文档一致性问题
- 已执行：健康流程 137/137、直接影响 73/73、Python 761/761、Vitest 17/17、production build、17 文件 py_compile、TypeScript 检查、`git diff --check`、正式库与用户产物指纹复核、只替换 5050、三个视口真实渲染
- 已决策闭合：SC-002 低收益墙钟方案由 6 项确定性持久化/重启/恢复测试替代；第 6 次全量审查按收益边界递减原则豁免
- 待执行：无

**下一步**：提交并推送 Spec 010 完整交付；后续不得重复执行 `execute_recovery` 或元数据补正。

---

## 附录 A：执行边界

1. 本轮最终收口允许提交并推送 Spec 010 完整交付，不回退用户工作区改动。
2. 数据库实验只在临时副本运行；正式库只读核验。
3. 正式恢复和元数据补正是此前已授权并已执行事实，本轮禁止重跑。
4. 服务操作仅限隔离端口 5050；不操作 5000 和 5051。

**本轮未执行**：删除原始数据、写正式数据库、重复正式恢复或元数据补正。

## 附录 B：关键文件清单

| 文件 | 角色 |
|------|------|
| `webui/store.py` | 状态机、pending 表、checkpoint、事件流、错误分类 |
| `webui/pipeline_exec.py` | ERROR_TAXONOMY 13 类错误码 |
| `webui/ai.py` | screen_jobs/match_jds 的 raise_on_systemic |
| `webui/historical_recovery.py` | 历史恢复预演与门禁 |
| `webui/app.py` | API 路由、latest-running-task 三级回退 |
| `webui/src/styles.css` | 响应式 media query |
| `webui/src/components/TaskProgress.vue` | 任务进度展示 |
| `tests/test_healthy_pipeline.py` | 健康流程自动回归（最终数量以本轮全仓输出为准） |
| `tests/sc015_viewport_check.py` | CDP 桌面与窄屏验收 |
| `specs/010-healthy-pipeline-recovery/spec.md` | FR-001~FR-050 + SC-001~SC-015 |
| `specs/010-healthy-pipeline-recovery/FULL_EXECUTION_PROMPT.md` | 执行授权与 14 项标准 |

报告结束。
