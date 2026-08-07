# Task 001：BOSS welfare 持久化进 extra

**所属 Wave**：1（后端，并行独立） | **用户故事**：US4（BOSS 福利数据补齐）

## 必读文件

- `specs/004-job-list-filter-sort/spec.md`（US4 验收）、`research.md` §1.3（数据链路）、`contracts/filter-sort.md` §1/§2（消费方合同）
- `webui/source.py`（`_normalize_job_fields`，L530/L1548，T133 归一化处）
- `webui/pipeline_exec.py`（列表→展示快照组装，`_job_exp_degree_codes` L660 为同层参考）
- `scripts/boss_cdp_raw.py` L456-471（BOSS 列表输出 `welfare` 格式：`' | '` 分隔字符串）
- `webui/store.py`（`extra_json` 持久化，L2561-2606 写入 / L168-222 读取，只读理解）
- `specs/001-add-zhilian-platform/contracts/http-api.md`（岗位对象合同，本次补充对象）

## 写入范围（互斥）

`webui/source.py` 或 `webui/pipeline_exec.py`（二选一，选归一化/展示快照组装点，不重复实现）、`specs/001-add-zhilian-platform/contracts/http-api.md`（仅岗位对象合同 extra 说明）、`tests/` 下对应测试文件（新增或扩展）。**禁止**修改 `store.py`、`scripts/boss_cdp_raw.py`、前端任何文件。

## 原子清单

- [ ] T001 定位 BOSS 列表岗位从抓取输出到展示快照（`JobDisplay.extra`）的组装点，确认 `welfare` 键在哪个 dict 中存活；记录选定的修改位置与理由（归一化层 vs 快照组装层）
- [ ] T002 [P] 先失败测试：BOSS 岗位带 `welfare: "五险一金 | 双休"`，走归一化/组装链路后 `extra["welfare_list"] == ["五险一金", "双休"]`（拆分符 `' | '`，strip 空段）
- [ ] T003 [P] 先失败测试：`welfare` 缺失/空字符串时 `extra` 不含 `welfare_list` 键；智联岗位（无 welfare）不受影响、不报错
- [ ] T004 实现 welfare 拆分写入 `extra["welfare_list"]`，复用既有 extra 组装路径（`extra` 全链路已持久化，不新增列）
- [ ] T005 更新 `http-api.md` 岗位对象合同：`extra` 补充说明「BOSS 专属可选键 `welfare_list: string[]`（福利标签）；缺失或为空时不提供，不编造」
- [ ] T006 回归：workbench/store/平台相关既有测试全绿；`uv run python -m unittest tests.test_repo_hygiene` 通过
- [ ] T007 提交：仅本包文件，信息 `feat: persist boss welfare into job extra`

## 完成定义

T002/T003 测试全绿；BOSS 结果快照含 `welfare_list`，智联与旧结果不含；无迁移、无新列；合同文档已同步；卫生测试通过。

## 提交纪律

只暂存本包文件；commit email `czyooutzilas@gmail.com`；提交前 `git diff --check` 与 `git status --short`。