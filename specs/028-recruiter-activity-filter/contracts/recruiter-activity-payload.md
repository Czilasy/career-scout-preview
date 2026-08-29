# Contract: 招聘者活跃数据采集载荷（028）

## Boss 详情产物增量（抓取子进程 → webui）

`scripts/boss` 详情抓取产物（`build_detail_record` dict）新增键：

```json
{"recruiter_activity_text": "刚刚活跃"}
```

- 取自详情页招聘者名片活跃行（「姓名 | 活跃文本 | 公司 | · | 头衔」区块）。
- 无名片 / 无活跃行 / 区块无法定位 → 空字符串 `""`（未知兜底），不报错。
- 仅截获原始文本；文本 → 天数区间的归一化在 webui 侧完成。

## 智联详情产物增量（抓取子进程 → webui）

`scripts/zhilian_cdp_raw.py` 详情 dict 新增键：

```json
{"recruiter_activity_text": "今日活跃", "recruiter_last_online_ms": 1756000000000}
```

- 时间戳取 `window.__INITIAL_STATE__.jobDetail.staff.lastOnlineTime`（毫秒）。
- 状态文本取 `jobDetail.staff` 内既有文本字段，仅作展示；取不到时为 `""`。
- 提取 JS 与合并逻辑位于新模块 `scripts/zhilian/detail_fields.py`（超标文件分流），`zhilian_cdp_raw.py` 仅 import + 拼接。

## webui 归一化输出

`webui/recruiter_activity.py` 提供平台无关归一化：

```
normalize_detail_activity(platform: str, detail: dict) -> dict | None
```

- Boss：按 D1 映射表把 `recruiter_activity_text` 转区间事实；表外文本 → `known=false`。
- 智联：`last_online_ms` 换算精确天数区间；无时间戳 → `known=false`。
- 输出 None（detail 无任何活跃键）或事实字典（结构见 data-model.md）。

## 合并与持久化

- `webui/pipeline_exec_details.py`：详情成功 → `job["extra"]["recruiter_activity"] = 事实字典`（内存链路：精筛硬规则读取）+ `store.update_job_extra(platform, job, {"recruiter_activity": 事实字典})`（jobs.extra_json 持久化）。
- 失败语义：store 更新失败仅记日志，不中断流水线（数据仍在内存链路可用）。
- 仅新抓取生效：无任何回填任务；手动重抓详情走同一链路自然更新。
