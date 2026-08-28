# Data Model: 测试大文件拆分重构（027）

本 Spec 为纯测试代码结构重构：**不改数据库 schema、不改持久化数据格式、不改产品代码**。涉及三类工作实体（均为过程产物，非持久化）：

## 基线快照（baseline snapshot）

| 组成 | 内容 | 存放 |
|---|---|---|
| 总数 | 收集到的用例总数（2026-08-28 实测 1786；以 B0 开工时重测为准） | 快照文件首行/随附记录 |
| 清单 | 全部用例的「类名.方法名」条目，含重复、排序 | 系统临时目录（不进仓库） |

- **规则**: 快照只在 B0 拍一次；此后每批与终检都以它为唯一基准做排序逐行 diff；模块路径不进条目（路径变化是拆分预期结果）。
- **状态转移**: 一次性生成 → 只读参照，任何批次不得修改快照本身。

## 拆分批次（batch）

| 字段 | 说明 |
|---|---|
| 范围 | 一个原巨型文件（或含其共享帮手抽离） |
| 交付物 | 新子目录及域文件 + 原文件删除 |
| 门禁 | 聚焦测试 → 后端全量 → 清单对账零差异 → 行数核对 → 卫生 → `refactor` 提交 |
| 不变量 | 任何批次边界：清单与快照零差异、全量绿、产品代码零改动 |

## 共享帮手模块

| 模块 | 内容 | 引用方向 |
|---|---|---|
| `tests/healthy_pipeline/harness.py` | `_load_boss_cdp_raw`、`_load_sc015_viewport_check`、`_make_app`、`_authed_test_client`、`_wait_for_pipeline_task`、`_pause_run` | 域内拆分文件 → harness（单向） |
| `tests/tuning/builders.py` | `_sample_nine_fields`、`_expected_path_digest`、`_make_valid_manifest_payload`、`_make_valid_report_payload`、`_CleanContextFakeExecutor` | 域内拆分文件 → builders（单向） |

- **规则**: 帮手模块只 import 标准库与产品代码，不反向 import 域内测试文件；非 `test` 前缀，不被收集；语义与抽离前逐字一致。
