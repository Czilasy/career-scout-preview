# 契约：手动恢复工具 CLI

**适用**：批次 7。`scripts/maintenance/historical_recovery.py` 迁入后的命令面。

## 子命令

```
uv run python -m scripts.maintenance.historical_recovery preview  [--db PATH] [--result-dir PATH]
uv run python -m scripts.maintenance.historical_recovery prepare  [--db PATH] [--result-dir PATH]
uv run python -m scripts.maintenance.historical_recovery execute --backup-id ID [--db PATH] [--result-dir PATH]
```

- `preview`：只读预演两个历史 run（15847d27… 与 e6250f0e…，默认内置，与迁出前 API 行为等价）；输出 verdict 计数、run 摘要与差异清单（JSON，字段与原 `/api/recovery/preview` 的 `preview` 载荷一致）。
- `prepare`：生成服务器侧 SQLite 备份 + manifest（SHA256/quick_check/版本校验规则原样保留）；输出 `backup_id`。
- `execute`：按 `backup_id` 执行恢复；破坏性动作，需显式 `--confirm` 才执行写库（新增安全栏，替代原"直接 POST 即执行"的接口形态）。
- `--db` 默认 `~/.career-scout/webui/webui.db`；**必须指向隔离测试库做演练**（db_meta env=test 校验沿用 `scripts/db_info.py` 口径）。
- 退出码：0 成功；2 参数/校验失败；3 数据校验失败（SHA256/quick_check/版本不一致）。

## 不变量

- 迁入只搬代码：修复逻辑、审计输出、manifest 结构与 `webui/historical_recovery.py` 逐行等价；唯一新增是 argparse 外壳与 `--confirm` 栏。
- 原 API 的调用方效果 = `preview` 子命令 stdout JSON（人工消费，无机器调用方）。
