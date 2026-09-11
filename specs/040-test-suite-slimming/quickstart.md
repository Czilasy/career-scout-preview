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

- 批一结果：
- 批二结果：
- 批三结果：
- 批四结果：
- 批五结果：
- 全量收尾对比（文件数/行数/用例数/时长）：
