# 契约：模块兼容性承诺

**适用**：批次 3-9 全部触及的模块。本契约是"只搬代码不改行为"的可检验形式：旧 import 路径与旧符号名在整个 031 生命周期内保持可用。

## 兼容 re-export 承诺表

| 门面/入口 | 承诺 | 依据测试 |
|---|---|---|
| `webui/app.py` | 旧符号（`_MSG_TASK_NOT_FOUND`、`_OPERATIONAL_ERRORS`、`_FEEDBACK_ERROR_STATUS`、`LOG_TAIL_LINES`、`_public_task_status` 等）继续可 `from webui.app import`；re-export 块集中且注释标注"兼容层，勿新增" | 既有 webui_app 集成套件 + B3 翻转后 grep 验收 |
| `scripts/boss_cdp_raw.py` | 全部旧符号可 import；CLI 入口行为不变 | tests/source/test_source_boss.py、tests/chrome_setup/ |
| `scripts/zhilian_cdp_raw.py` | `__getattr__` 代理全部旧符号（fetch_list、fetch_detail、scrape_details_batch、check_login_state_tri、preflight、is_zhilian_host、input_hash 等）；消费方旧 import 不改也可用，新代码一律 import 新模块 | tests/source/test_source_zhilian.py、tests/test_zhilian_risk_signal.py |
| `webui/task_runners.py` | `TaskRunner`、`WorkbenchRunner`、全部助手旧名可 import；`app.py` 的 `from webui.task_runners import TaskRunner, WorkbenchRunner` 不变 | tests/webui_app/test_webui_app_taskrun.py |
| `webui/pipeline_context.py` | B9 之前维持现状；B9 移除 `__getattr__` 时同步迁移全部调用点与测试，批内自洽 | tests/webui_app/ 打桩迁移用例 |

## 禁止事项

- 兼容 re-export 块内禁止新增任何逻辑（宪法原则 VI）。
- 禁止"顺手"收紧旧符号的可见性（下划线名仍按下划线名导出）。
- 测试断言只随事实迁移，不得放松（quickstart B8/B9 复核）。
