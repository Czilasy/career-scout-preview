# Quickstart: 健康流程补救与优化

**Date**: 2026-07-28

本文档定义可运行的验证场景，证明健康流程端到端工作。不包含完整实现代码。

## 前置条件

1. Python 3.11 + `.venv` 已安装依赖
2. Node 20+ 已安装
3. Chrome CDP 调试端口 9222 可用（真实抓取场景）
4. 正式数据库已备份（阶段 0 已完成）
5. WebUI 服务运行在端口 5000

## 验证命令

### 自动测试

```powershell
# Python 语法检查
.\.venv\Scripts\python.exe -m py_compile scripts\boss_cdp_raw.py

# Python 单元测试
.\.venv\Scripts\python.exe -m unittest discover -s tests

# 前端测试
cd webui
npm test -- --run

# 前端构建
cd webui
npm run build
```

### 服务重启（五步，缺一不可）

```powershell
# 1. 查端口占用
Get-NetTCPConnection -LocalPort 5000

# 2. 杀旧进程
Stop-Process -Id <旧PID> -Force

# 3. 启动新服务
.\.venv\Scripts\python.exe webui\app.py

# 4. 确认新 PID 在听
Get-NetTCPConnection -LocalPort 5000

# 5. 浏览器访问确认
curl http://127.0.0.1:5000/api/version
```

## 端到端验证场景

### 场景 1：第 800/1,408 触发验证码（SC-006）

**前置**：准备 1,408 条待抓 JD 的任务，在第 800 个处理单元注入验证码。

**步骤**：
1. 启动 JD 抓取任务
2. 第 800 个触发验证码
3. 检查任务状态

**预期**：
- 任务状态 = `paused`
- 成功 = 762，失败 = 38，未开始 = 608
- 浏览器保持打开
- `GET /api/task-state/<run_id>` 返回 `pause_info.error_code = "captcha_required"`

**验证命令**：
```powershell
curl http://127.0.0.1:5000/api/task-state/<run_id> | python -m json.tool
```

### 场景 2：暂停持久化、重启恢复与手动继续（SC-002）

**前置**：场景 1 的暂停任务。

**步骤**：
1. 不点击继续
2. 重新打开存储，并通过刷新页面或重启隔离服务重新读取任务
3. 核对任务身份、阶段、进度、暂停原因和 checkpoint
4. 用户明确触发继续，核对恢复输入和重复处理数

**预期**：
- 重新读取后仍是同一 `paused` 任务，不自动继续或完成
- 阶段、已处理数、待处理数、暂停原因和 checkpoint 不丢失
- 手动继续从原 checkpoint 恢复，已完成工作重复处理数为 0

**确定性验收**：运行暂停持久化、真实应用重启、刷新恢复、canonical 任务身份和重启状态迁移 6 项回归测试；必须全部通过。原 24 小时静态数据库轮询已按 2026-07-28 验收修订退役，不作为完成门禁。

### 场景 3：继续后零重复（SC-004）

**前置**：场景 1 的暂停任务，用户已完成验证码。

**步骤**：
1. `POST /api/task/continue/<run_id>`
2. 等待任务完成
3. 查询重复处理数

**预期**：
- 已成功的 762 个岗位重复处理数 = 0
- 608 个未开始岗位被处理

### 场景 4：暂停后刷新页面恢复（SC-003）

**前置**：场景 1 的暂停任务。

**步骤**：
1. 刷新浏览器页面

**预期**：
- 10 秒内重新看到同一任务的阶段、进度、暂停原因
- 继续按钮可见

### 场景 5：暂停后重启服务恢复（SC-003）

**前置**：场景 1 的暂停任务。

**步骤**：
1. 五步重启 WebUI 服务
2. 刷新浏览器

**预期**：
- 任务状态恢复为 `paused`
- 继续能力保留
- `GET /api/latest-running-task` 返回 paused 任务

### 场景 6：短 JD 30/80/119 字通过（SC-009）

**前置**：准备 3 条真实短 JD（30字、80字、119字）。

**步骤**：
1. 对 3 条短 JD 运行 `extract_job_description`
2. 检查是否被接受

**预期**：
- 3 条均通过（不因字数被拒绝）
- 登录墙/导航壳/风控页仍被拒绝

### 场景 7：统计分类总和等于总数（SC-007）

**前置**：任意完成的任务。

**步骤**：
1. `GET /api/task-state/<run_id>`
2. 核对 `match + mismatch + pending + dropped == total`

**预期**：完全一致

### 场景 8：历史恢复只读预演（SC-011）

**前置**：run `e6250f0ed794492180269de050bfd41a`。

**步骤**：
```powershell
curl http://127.0.0.1:5000/api/recovery/preview/e6250f0ed794492180269de050bfd41a
```

**预期**：
- total = 1926
- matched = 198, mismatched = 514, dropped = 518
- pending_50_struct = {total: 50, match: 17, mismatch: 33}
- pending_646_no_jd = {total: 646, failed_code: historical_reason_unavailable,
  reason: 旧流程未保存具体失败原因, next_action: recrawl_jd}
- 不得把这 646 条猜分为详情无效、验证码失败和未开始；只能在重新抓取 JD 时实时分类
- conservation_ok = true

### 场景 9：正式恢复 696 条（SC-012, SC-013）

**前置**：场景 8 预演通过 + 全部门禁满足。

**步骤**：
```powershell
curl -X POST http://127.0.0.1:5000/api/recovery/prepare/e6250f0ed794492180269de050bfd41a

curl -X POST http://127.0.0.1:5000/api/recovery/execute/e6250f0ed794492180269de050bfd41a `
  -H "Content-Type: application/json" `
  -d '{"backup_id":"<prepare 返回的 backup_id>"}'
```

**预期**：
- 50 条修正为 17 match + 33 mismatch（无 AI 调用）
- 762 条 JD 保留未重抓
- 646 条交给新流程
- 总数守恒、无重复、无丢失

### 场景 10：版本标识可见（SC-014）

**步骤**：
```powershell
curl http://127.0.0.1:5000/api/version
```

**预期**：
- 返回 backend_version + build_hash
- 前端页脚显示版本
- 重启后版本更新

## 集成验收（约 90 条）

在自动测试和小规模真实验收通过后，执行约 90 条岗位的完整流程：

1. 列表抓取 90 条
2. AI 粗筛
3. JD 详情抓取（含注入验证码测试暂停/继续）
4. AI 精筛
5. 验证统计守恒
6. 验证暂停/继续/刷新/重启恢复
7. 验证批量重抓和单条补抓

**通过标准**：SC-001~SC-015 全部满足，无伪装完成，无重复处理。
