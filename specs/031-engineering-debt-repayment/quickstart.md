# Quickstart: 工程还债——逐批验证指南

**前置**：Windows 开发机；`uv sync`；前端 `cd webui && npm ci`。所有命令在仓库根执行。

## 通用门禁（每批次收尾必跑）

```powershell
# 后端全量测试
uv run python -m unittest discover -s tests
# 前端测试 + 类型门禁构建
cd webui; npm test; npm run build; cd ..
# 仓库卫生
uv run python -m unittest tests.test_repo_hygiene
# whitespace
git diff --check; git diff --cached --check
```

## 批次专属验证

### B1 改旧改错

- `grep -rn "1\.8\.1" README.md` → 无矛盾陈述（标题与正文版本一致）
- `grep -rn "49\.232\.60\.135" --include="*.yml" --include="*.ps1" --include="*.py" --include="*.md" .` → 0 处
- `git ls-files .specify` → 含 `memory/constitution.md`
- `ls sonar-project.properties` → 不存在
- 卫生测试含 dist 文本产物凭据扫描用例

### B2 发布验证闭环

- 推送后 GitHub Actions：ci.yml 出现 windows-latest 作业且全绿
- 本地：`pwsh scripts/release_check.ps1` 在"CHANGELOG 最新版本无标签"构造态下失败；打标后通过
- `grep -n "unittest" .github/workflows/release-macos.yml` → build 作业前存在测试 job

### B3 主文件松绑

- `grep -rn "from webui.app import\|import webui\.app" webui scripts --include="*.py"` → 仅 app.py 自身与兼容 re-export 块命中
- `grep -rn "_MSG_TASK_NOT_FOUND = " webui --include="*.py"` → 唯一定义
- `grep -rn "避免与 webui.app 循环" webui --include="*.py"` → 0 处
- `grep -c "register_blueprint" webui/app.py` → 0；`grep -rn "def register_.*_routes" webui/*_api.py` 全覆盖
- 聚焦：`uv run python -m unittest tests.webui_app.test_webui_app_core` 等路由域测试

### B4 错误留证据

- AST 复核（与审查同口径）：

```powershell
uv run python - <<'PY'
import ast, pathlib
n=0
for p in list(pathlib.Path("webui").rglob("*.py"))+list(pathlib.Path("scripts").rglob("*.py")):
    t=ast.parse(p.read_text(encoding="utf-8",errors="replace"))
    for x in ast.walk(t):
        if isinstance(x,ast.ExceptHandler) and len(x.body)==1 and isinstance(x.body[0],ast.Pass):
            ty=ast.unparse(x.type) if x.type else "bare"
            if ty in("Exception","BaseException","bare"): n+=1
print("pass-only:",n)
PY
```

→ ≤ 卫生测试基线；白名单条目与代码注释一一对应
- 抽查 3 处留痕：日志文件出现对应 warning/debug 行
- `grep -rn "^\s*print(" webui --include="*.py"`（业务模块）→ 仅 CLI 入口豁免清单内
- `uv run python -m unittest tests.test_updater` → 含状态目录环境变量新用例

### B5 爬虫自管

- `grep -rn "_facade()" scripts/boss --include="*.py"` → 0 处
- `uv run python -c "import scripts.boss.search, scripts.boss.detail_scrape"` → 独立 import 成功
- 聚焦：boss 相关源测试 `tests/source/test_source_boss.py`

### B6 后端超限拆分

- `wc -l scripts/zhilian_cdp_raw.py webui/task_runners.py` → ≤150 / ≤400
- `uv run python -c "import scripts.zhilian_cdp_raw as z; z.fetch_list; z.scrape_details_batch"` → 旧路径可用
- `wc -l scripts/zhilian/*.py webui/task_runner_support.py webui/workbench_runner.py` → 全部 ≤800
- 聚焦：`tests/source/test_source_zhilian.py`、`tests/healthy_pipeline/`

### B7 事故代码退场

- 路由表无 recovery：启动测试客户端 GET `/api/recovery/preview/x` → 404
- `uv run python -m scripts.maintenance.historical_recovery preview --db <测试库>` → 预演输出等价
- `ls webui/historical_recovery.py` → 不存在

### B8 前端理线

- `grep -rn "deps: any\|: any" webui/src --include="*.ts" --include="*.vue" | grep -v __tests__` → 0
- `wc -l webui/src/views/DiscoveryView.vue` → ≤1200
- `npm run build`（含 vue-tsc）通过；构建后样式资源与理线前对比：`git diff webui/dist` 无样式规则变化
- `npm test` 533 用例全绿

### B9 测试打桩收尾

- `grep -rn 'patch("webui.app\|patch(\x27webui.app' tests webui --include="*.py"` → 0
- `grep -n "__getattr__" webui/pipeline_context.py` → 0（动态门面移除）
- 后端全量全绿

## 关单总检

1. 上述 B1-B9 全部通过并逐批有提交
2. `uv run python scripts/db_info.py` 确认测试隔离库未受影响（env 标志机制未动）
3. 三道保险复核：全量测试全过 / `git diff <起点>..HEAD -- webui/src/styles.css` 为空且 UI 无许可外变化 / 每批提交独立 `git revert` 可回退
4. 宪法红线复核：`wc -l` 全仓 Python 业务文件 ≤800、Vue ≤1200
