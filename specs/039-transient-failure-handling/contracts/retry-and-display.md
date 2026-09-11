# Contracts: 重试通道与失败展示

## 1. 内部模块契约（新增）

### `webui/pipeline_exec_retry.py`

```python
# 偶发可重试失败码白名单（注册表正名码）
TRANSIENT_RETRY_CODES: frozenset[str]
# = {"source_timeout", "source_unreachable", "source_invalid_output",
#    "source_unknown_error", "source_result_write_failed"}

def is_transient_retryable(failed_code: object, *, retry_used: bool) -> bool:
    """失败码属偶发白名单且本组合尚未使用重试额度时返回 True。"""

def retry_event_payload(*, failed_code: str, reason: str) -> dict:
    """构造白箱 retry_scheduled 事件的 payload（safe，不含敏感内容）。"""
```

- 调用方：仅 `webui/pipeline_exec_search.py`（单向调用）。
- 禁止：反向 import、被路由层直接调用。

### `webui/source_zhilian_runtime_adapter.py`（新增函数，镜像详情侧既有模式）

```python
def build_zhilian_list_signal_map(
    base: Mapping[str, str | None],
) -> dict[str, str | None]:
    """在既有列表 signal 映射上补齐 cdp_unavailable → source_cdp_unavailable。"""
```

- 调用方：仅 `webui/source_zhilian_cdp.py`（单向调用，与详情侧同构）。
- 语义：补齐后列表抓取的“连不上浏览器”具备注册表名称，并自动获得浏览器失联恢复资格。

## 2. 内部行为契约（既有模块，行为扩展）

### `webui/pipeline_exec_search.py` — 组合失败处理

- 顺序（保持既有先后）：首次尝试 → 登录失效复核（既有）→ 空结果复核（既有）→ 浏览器失联重启重试（既有）→ **偶发一次重试（新增）** → 失败归类（既有）。
- 重试额度：`retry_used` 标志由上述三条重试路径共享；任一触发后其余路径不再重试。
- 偶发重试行为：
  1. 写白箱 `retry_scheduled`（info）；
  2. 应用断点字段（恢复起始页 + 已抓岗位快照）；
  3. 再次执行一次抓取尝试；
  4. 成功 → 走既有完成路径（`evidence.completed`）；失败 → 走既有失败归类（`evidence.failed`，跳过语义）。
- 禁止：修改抓取逻辑本身；新增第二次以上重试。

## 3. UI 契约（既有接口 `/api/task-state/<run_id>`，不改接口）

消费字段（均已存在）：

| 字段 | 含义 | 本次展示用途 |
|---|---|---|
| `fail_count` | 跳过（重试后仍失败）的组合数 | 失败数字（悬停入口） |
| `success_count` | 抓完的组合数 | 收尾后“已完成”计数 |
| `total` | 组合总数 | 计数分母 |
| `combo_issues[]` | `{combo_key, code, code_text, reason, ts}`，倒序，最多 20 条 | 悬停浮窗逐条内容 |

展示约束（`webui/src/components/TaskProgress.vue`）：

1. 不再渲染成排失败明细列表。
2. 失败仅以数量呈现；无失败时不显示该数字及其悬停入口。
3. 悬停浮窗逐条显示 `组合名：简单原因`（`code_text` 优先，缺失时用 `reason`）；最多 5 条，超出显示“另有 N 条”。
4. 文案约束：不使用“已跳过”等词语替代原因；不以“超时抓取”式生硬词给失败或结果定性。
5. 收尾后计数自洽：已完成（success_count）+ 跳过（fail_count）+ 未开始 = 总数；未开始不再恒等于总数。
