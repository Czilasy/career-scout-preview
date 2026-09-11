# Quickstart: 测试体系低噪音瘦身

## 通用验证命令

```powershell
# 后端受影响子集（示例：批一聚焦）
uv run python -m unittest tests.test_pipeline_guard -v

# 后端全量（批末执行；依赖 webui/dist 已构建）
uv run python -m unittest discover -s tests

# 前端测试与构建（批末执行）
cd webui; npm test; npm run build

# 仓库卫生（每批执行）
uv run python -m unittest tests.test_repo_hygiene

# 规模统计（每批收尾）
(Get-ChildItem tests -Recurse -Filter *.py).Count
((Get-ChildItem tests -Recurse -Filter *.py | Get-Content) | Measure-Object).Count
(Get-ChildItem webui/src -Recurse -Filter *.spec.ts).Count
((Get-ChildItem webui/src -Recurse -Filter *.spec.ts | Get-Content) | Measure-Object).Count
```

## 故障注入指纹操作模板（合并/挪动类改动必用）

1. `git status --short <产品文件>`：确认目标产品文件干净（无未提交改动）。
2. 对目标逻辑做最小注入（例：将某判定改为恒真/恒假）。
3. 跑"受影响测试集"（= 该产品符号的引用方测试文件），记录**变红用例清单**（改动前指纹 A）。
4. `git checkout -- <产品文件>` 还原；`git status` 复核干净。
5. 完成测试侧改动后，重复第 2–3 步（同注入点），记录**变红用例清单**（指纹 B）。
6. 比对：A = B 通过；不一致 → `git checkout --` 回滚该处测试改动，登记为"不可合并"并上报。

## 每批验证场景与预期

### 批一（假保护必修）

- S1 `test_pipeline_guard` 白箱用例：修复后注入（阻断白箱事实写入）→ 该用例必须变红（修复前不会）。
- S2 `test_webui_app_runtime` 隔离：跑该文件期间不再向本机端口发请求、不再读真实用户目录（打桩断言）。
- S3 门禁选择器：`sc015_viewport_check.py` 与 `test_pipeline_state.py` 使用当前真实元素名；全仓旧名 0 命中。
- S4 桌面壳：用例使用临时 `state_dir`；真实 `~/.career-scout` 零写入（运行前后对比目录）。
- S5 恒真/名实不符修复：各自注入对应故障后变红。

### 批二（零风险删除）

- 每条删除前：`rg` 全仓检索 0 命中（记录检索式）。
- 批末：全量 + 前端 + 构建 + 卫生全绿；行数统计下降。

### 批三（重复选边）

- 每组：指纹 A/B 一致；正本侧断言只增不减。
- 重点组：`test_webui_app_semantics.py` 双跑消除后全量时长不增加。

### 批四（缠结小修）

- 受影响文件单独跑、换序跑、重复跑各一次，结果一致。
- 构建指纹：改一个 spec 文件后，`build-state.json` 的 frontend 指纹**不变**（排除生效）。
- `hooks/pre-commit`：本机路径 0 命中；卫生检查照常工作。

### 批五（死代码回收）

- 前置：登记册整理成清单 → 用户逐项同意。
- 删除后：全量 + 前端 + 构建全绿；`rg` 复核被删符号 0 命中。

## 结果记录区（实施时填写）

- 批一结果：完成（2026-09-11；23 项全处置，含双轴独立审查后补验）。
  - 修复（12 条）：白箱零执行用例改真服务+真库读回事实（T001）；runtime 三条补五类隔离桩（T002）；sc015 门禁选择器对齐真实元素+新守卫（T003/T004）；桌面壳关闭用例改临时 state_dir（T005）；恒真/名实不符逐条改造（T007/T008/T009/T010/T011/T012/T013/T018/T019/T020）。
  - 删除（3 条）：2 条永久跳过（T006）+ 1 条恒真 `>=1`（T009）。
  - 解缠：3 处日志目录独立隔离（T014）；猴子补丁补全还原（T015）；2 处前端顺序依赖消除（T016/T017）；2 个 main-guard 挪至文件尾（T022）。
  - 注入验证（注入→必红→还原，逐条）：T001 阻断白箱写入✓；T003/T004 选择器回退旧名✓；T007 待筛选文案改名✓（3 例红）；T008 跳过不可用判定✓（恰 2 例红）；T009 读取器加写 API✓；T010 不落盘✓；T011 禁重建✓；T012a 去等待✓；T012b README 版本改旧✓；T013 重复登记迁移✓（先证旧断言不红→补强"记录数不变"后红）；T018 真实返回点改 404✓；T019 未知 run 写记录✓；T020 文案改动✓。
  - 其它验证：T016 单跑无预热重放（旧写法红/新写法绿）；T022 直跑收集全量（candidate 123 例、execution_config 77 例）；T014/T015 组合运行一致；S4 真实用户目录跑前后零变化（12913 文件、desktop_window.json mtime 均不变）。
  - 回归：后端受影响子集 + 全量 3160 例全绿（-3 = 删的 3 条；skip 5→1）；前端 878 例全绿；构建成功；卫生 14 例全过。
  - 偏差与补记（2026-09-11 双轴审查发现后处置）：T007 原任务入口实测不存在（仅历史模式渲染）→ 修正用例语义为只读浏览契约+正锚点、删恒真断言；T020"结构化断言"受生产字符串日志接口限制→收窄为完整文案绑定；T015 未改 patch.object 模式（泄漏已消除，属任务定义外）；T013 由字面替换补强为真断言；T011 补 try/finally（失败路径释放句柄）；test_candidate 末尾多余空行清理。
- 批二结果：
- 批三结果：
- 批四结果：
- 批五结果：
- 全量收尾对比（文件数/行数/用例数/时长）：
