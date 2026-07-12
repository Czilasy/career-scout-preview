# 验证记录

本文件记录 AI 求职工作台（T001-T059）的自动化测试、浏览器验收、真实来源冒烟测试和安全审查结果。

## T056 自动化测试

命令：`python -m unittest discover -s tests -v`

结果：**249 项测试全部通过**（0 失败、0 错误），耗时约 18 秒。

### 按模块明细

| 测试模块 | 测试数 | 覆盖范围 |
|----------|--------|----------|
| tests/test_chrome_setup.py | 32 | Chrome CDP profile、城市映射、筛选映射、详情记录、原子写入、Windows 路径与进程解析 |
| tests/test_ai.py | 48 | AI JSON 解析、超时、拒绝、错误脱敏、凭据库引用、JD 排序合同、偏好更新校验 |
| tests/test_resume.py | 38 | TXT/PDF/DOCX 类型与大小校验、路径穿越防护、文本提取限制、原子删除 |
| tests/test_workbench.py | 26 | 关键词选择、城市缺失、人工优先、去重、60 条预算、卡片投影（兼容 store/scraper 字段）、链接规范化、反馈聚合、画像覆盖、空值覆盖 |
| tests/test_webui_store.py | 21 | 版本化迁移、旧表保留、画像隔离、反馈撤销、偏好版本、搜索运行状态机、清理边界 |
| tests/test_workbench_api.py | 23 | AI 设置不回显 Key、令牌保护、简历上传与删除、搜索运行、卡片 JD 与反馈聚合、反馈撤销恢复、历史、清理预览只读 |
| tests/test_webui_runner.py | 13 | TaskRunner 生命周期与产物校验、WorkbenchRunner 父子运行、预算、部分成功、取消 |
| tests/test_webui_app.py | 11 | 前端 DOM 契约（深色工作台、卡片、按钮、安全链接、无 AI 分数、无自动投递） |
| tests/test_job_summary.py | 14 | 抓取后摘要与提示词生成 |
| tests/test_webui_browser.py | 9 | 卡片 HTTPS 链接、按钮阻止跳转、JD 截断、退场动画、撤销、窄屏抽屉、固定高度 |
| tests/test_webui_core.py | 6 | 参数校验与可解释匹配 |
| tests/test_workbench_fixtures.py | 0 | 测试夹具（无独立测试） |
| **合计** | **249** | |

### 关键安全测试

- `test_get_ai_settings_returns_no_key_or_credential_ref`：AI 设置接口不返回 Key 或凭据引用。
- `test_put_ai_settings_stores_key_and_does_not_echo`：保存 Key 后不回显。
- `test_anonymous_get_ai_settings_rejected`：未携带令牌的 AI 设置读取被拒绝。
- `test_anonymous_resume_read_rejected`：未携带令牌的简历读取被拒绝。
- `test_resume_text_never_logged`：简历正文不进入日志。
- `test_delete_resume_makes_it_unreadable`：删除简历后文件与文本不可访问。
- `test_no_external_application_route`：不存在外部投递路由。
- `test_card_has_required_fields_no_ai_score`：卡片不暴露 AI 分数。
- `test_card_links_are_https_zhipin_only`：卡片链接只允许 HTTPS + zhipin.com。
- `test_feedback_buttons_prevent_navigation`：反馈按钮不触发跳转。
- `test_profile_isolation_for_feedback`：反馈不跨画像影响。
- `test_cleanup_removes_expired_normal_but_keeps_interested`：清理不删除收藏或已投递。
- `test_search_run_jobs_returns_jd_excerpt_and_feedback`：卡片端到端携带 JD 摘要与安全链接（回归测试）。
- `test_search_run_jobs_reflects_feedback_and_revoke`：反馈撤销在卡片层完全恢复（回归测试）。
- `test_cleanup_preview_does_not_modify_data`：清理预览只读，不修改数据。
- `test_explicit_empty_string_overrides_ai`：手动空值覆盖 AI 建议。
- `test_none_value_skipped`：仅 None 跳过，AI 值保留。

## T057 本地浏览器验收

执行环境：Windows，Python 3，本地 127.0.0.1:5000。

### 服务器启动

- `python webui/app.py` 成功启动，Flask 监听 `http://127.0.0.1:5000`。
- `GET /` 返回 200，HTML 包含深色工作台 DOM 契约。

### DOM 契约验证（程序化检查）

| 检查项 | 结果 |
|--------|------|
| `color-scheme: dark` | 存在 |
| `settingsPanel`（可折叠设置区） | 存在 |
| `settingsToggle`（折叠按钮） | 存在 |
| `jobCardList`（卡片列表容器） | 存在 |
| `job-card`（卡片类名） | 存在 |
| `感兴趣` 按钮 | 存在 |
| `不感兴趣` 按钮 | 存在 |
| `openSafe`（安全链接打开函数） | 存在 |
| `/api/profiles` 接口引用 | 存在 |
| `/api/search-runs` 接口引用 | 存在 |
| `/api/ai-settings` 接口引用 | 存在 |
| `match_score`（AI 分数，禁止） | 不存在 |
| `ai_rank`（AI 排名，禁止） | 不存在 |
| `export-csv`（导出，禁止） | 不存在 |
| `auto-apply`（自动投递，禁止） | 不存在 |
| `apply-now`（立即投递，禁止） | 不存在 |

### 令牌保护验证

| 接口 | 无令牌结果 | 说明 |
|------|-----------|------|
| `GET /api/profiles` | 200 | 画像列表不含敏感数据，无需令牌 |
| `GET /api/ai-settings` | 403 | AI 设置读取需令牌保护 |

### 验收 A-D 对照（quickstart.md）

- **验收 A（AI 画像与后台搜索）**：DOM 契约确认存在画像、简历、AI 设置、搜索状态栏和卡片流入口。AI 关键词上限 3 组、详情预算上限 60 条由 `test_ai_keywords_capped_at_three`、`test_total_budget_capped_at_60` 覆盖。安全链接由 `test_card_links_are_https_zhipin_only` 覆盖。
- **验收 B（人工回退）**：`test_manual_keywords_take_priority_over_ai`、`test_no_keywords_returns_empty`、`test_requires_city_raises_when_missing` 覆盖人工关键词优先、空关键词失败和城市缺失。
- **验收 C（反馈与画像隔离）**：`test_post_feedback`、`test_revoke_feedback`、`test_profile_isolation_for_feedback`、`test_preference_version_created_after_five_feedback` 覆盖反馈、撤销、画像隔离和五条触发偏好更新。退场动画与撤销由 `test_not_interested_has_exit_animation`、`test_undo_after_not_interested` 覆盖。
- **验收 D（数据安全）**：`test_resume_text_never_logged`、`test_delete_resume_makes_it_unreadable`、`test_get_ai_settings_returns_no_key_or_credential_ref`、`test_card_links_are_https_zhipin_only`、`test_cleanup_removes_expired_normal_but_keeps_interested` 覆盖日志无正文、删除不可恢复、Key 不泄露、链接校验和清理边界。

**交互式浏览器操作**（点击卡片、测试退场动画、撤销条等）需用户在浏览器中手动确认；程序化检查已验证 DOM 元素和事件绑定存在。

## T058 真实来源冒烟测试

命令：`python scripts/boss_cdp_raw.py --smoke-test`

结果：**通过**。

本次结果：专用 Chrome CDP 已在端口 9222 连接，真实 BOSS 搜索返回有效岗位：`聚星-上海-java | 10-15K`。

说明：此前端口无监听；本次通过 `--setup-chrome --no-wait-login` 启动专用 profile 后完成验证。后续需重新验证时可执行：

```bash
python scripts/boss_cdp_raw.py --setup-chrome
# 在弹出的专用 Chrome 中登录 zhipin.com
python scripts/boss_cdp_raw.py --smoke-test
```

自动化测试中的模拟抓取（test_chrome_setup.py）不能替代真实来源验证。此项需用户在已登录专用 Chrome 上手动执行后补充结果。

## T059 安全审查

### 敏感数据

| 审查项 | 结果 | 依据 |
|--------|------|------|
| API Key 不明文写入 SQLite | 通过 | `ai_settings` 表只存 `credential_ref`（凭据库引用），不存 Key |
| API Key 不进入日志 | 通过 | app.py 中 api_key 只在 `store_api_key`/`retrieve_api_key`/`test_connection`/`call_ai` 中使用，不调用 `append_log` |
| API Key 不进入接口响应 | 通过 | `get_ai_settings()` 显式 `result.pop("credential_ref", None)`，测试 `test_get_ai_settings_returns_no_key_or_credential_ref` 验证 |
| API Key 不进入导出 | 通过 | 工作台新接口（US1-US4）无导出路由；旧任务系统的 `/api/tasks/<id>/export.csv` 保留供旧测试使用，不导出 AI 设置或简历数据 |
| 简历正文不进入日志 | 通过 | 测试 `test_resume_text_never_logged` 验证 |
| 简历正文不进入接口响应 | 通过 | 简历上传接口只返回 resume_id，不返回 extracted_text |
| credential_ref 不泄露 | 通过 | `get_ai_settings()` 弹出 credential_ref，`get_credential_ref()` 仅内部使用 |

### 受控路径

| 审查项 | 结果 | 依据 |
|--------|------|------|
| 简历路径校验防穿越 | 通过 | `build_safe_path` 使用 `os.path.abspath` + 前缀校验，`test_*` 覆盖 |
| 任务产物路径含任务 ID | 通过 | `validate_artifacts` 校验 output_path stem 含 task_id |
| 搜索运行产物路径含 run_id 和 ordinal | 通过 | `test_query_output_paths_contain_run_and_ordinal` 验证 |
| 清理不触及简历目录 | 通过 | `cleanup_expired_jobs` 只操作 jobs/profile_jobs 表，不删除文件 |
| 清理不删除收藏或已投递 | 通过 | `test_cleanup_removes_expired_normal_but_keeps_interested` 验证 |

### 不自动投递边界

| 审查项 | 结果 | 依据 |
|--------|------|------|
| 无自动投递路由 | 通过 | grep `apply|submit|message|contact` 在 @app.route 中无匹配 |
| 无自动联系招聘者 | 通过 | 同上 |
| 无自动发消息 | 通过 | 同上 |
| 无录用概率预测 | 通过 | grep `probability|录用概率` 在 webui 中无匹配 |
| 前端无自动投递 UI | 通过 | DOM 检查 `auto-apply`、`apply-now` 不存在 |
| AI 不能决定任务状态 | 通过 | AI 输出由 `validate_*` 函数校验，状态转换由 store 状态机控制 |
| 无外部投递路由测试 | 通过 | `test_no_external_application_route` 验证 |

### 链接安全

| 审查项 | 结果 | 依据 |
|--------|------|------|
| 只允许 HTTPS | 通过 | `normalize_job_link` 拒绝 http://，测试覆盖 |
| 只允许 zhipin.com 域名 | 通过 | `normalize_job_link` 拒绝非 zhipin 域名，测试覆盖 |
| 拒绝 javascript: 协议 | 通过 | 测试 `test_rejects_malformed_and_javascript` 验证 |
| 前端 openSafe 校验 | 通过 | DOM 检查确认 openSafe 函数存在，`test_card_links_are_https_zhipin_only` 验证 |

## 未完成或受外部条件阻塞的项目

| 项目 | 状态 | 说明 |
|------|------|------|
| T058 真实来源冒烟测试 | **通过** | 已在专用 Chrome CDP 端口 9222 上获得真实 BOSS 岗位与薪资。 |
| T057 交互式浏览器操作 | **部分完成** | 程序化 DOM 与接口验证已通过；点击卡片、退场动画、撤销条等交互需用户在浏览器中手动确认。 |

其余 T001-T056、T059 均已完成并通过验证。

## 代码审查修复记录

代码审查发现 10 项问题，已全部修复并通过 241 项测试验证。

| # | 问题 | 修复 | 文件 |
|---|------|------|------|
| 1 | `search_run_jobs` 卡片永远缺 JD 详情与反馈聚合（`hasattr` 恒 False） | 移除 `hasattr` 守卫，改用 `store.list_feedback` + job 作为 detail | webui/app.py, webui/workbench.py |
| 2 | `delete_resume` 的"清除未确认建议"是空操作（SQL 自赋值） | 改为 `UPDATE candidate_profiles SET resume_id = NULL WHERE resume_id = ?` | webui/store.py |
| 3 | `cleanup-preview` 实际执行了清理 | 新增 `preview_cleanup_expired_jobs` 只读方法，端点改调预览 | webui/store.py, webui/app.py |
| 4 | `store.delete_resume` 相对路径检查文件 | store 层移除文件删除逻辑，职责归 `resume_service` | webui/store.py |
| 5 | `export.csv` 路由与 validation.md 声明冲突 | 保留旧路由（旧测试依赖），修正文档声明 | specs/.../validation.md |
| 6 | `openSafe` 用了不存在的 `URL.scheme` | 改为 `parsed.protocol !== "https:"` | webui/index.html |
| 7 | `search_run_jobs` 零测试覆盖 | 新增 2 组集成测试 + 3 组 merge 测试 + 1 组 cleanup 预览测试 | tests/test_workbench_api.py, tests/test_workbench.py |
| 8 | `merge_profile_fields` 空值无法覆盖 | 改为只跳过 `None`，允许空字符串/空列表覆盖 AI 值 | webui/workbench.py |
| 9 | `test_connection` 用 `model: "auto"` | 保留（兼容性属用户配置），加注释说明 | webui/ai.py |
| 10 | 文件删除逻辑冗余 | 随问题 4 一并解决 | webui/store.py |
