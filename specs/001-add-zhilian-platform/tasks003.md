# Tasks 003：智联元数据、城市与浏览器登录空间

## 新会话启动提示词

```text
请在当前仓库根目录执行 specs\001-add-zhilian-platform\tasks003.md。先读取仓库根目录 AGENTS.md、智联功能目录全部冻结工件、roadmap\REFERENCE_GET_JOBS.md 和 tasks001.md；正式实施前输出【已查阅】。

本会话只负责 tasks003.md。先现场核验 tasks001 的平台注册表、schema、城市、URL 合同、测试和提交；不完整时停止并报告，不得另造一套平台体系。历史参考只用于侦察，所有公司性质、城市码、页面 marker 和登录空间结论必须来自当前可验证证据。没有人工登录态或关键事实无法确认时，完成可独立验证的骨架和测试，但必须保持 zhilian.enabled_for_new_tasks=false，并明确记录阻断。不得保存 Cookie、完整页面、JD、账号或绝对 profile 路径。完成后只提交本任务改动，不 push，不自动执行 tasks004。
```

## 给独立执行 AI 的指令

本任务负责可变外部事实和平台登录空间，可在全新会话中执行。先读根 `AGENTS.md`、全部冻结工件、`roadmap/REFERENCE_GET_JOBS.md` 和 `tasks001.md`。历史参考只能作为侦察起点，不能作为当前事实。

## 总前置门禁

现场确认 `tasks001.md` 已提供平台注册表、schema、城市解析和 URL 规则接口，且其聚焦测试通过并已有提交。缺任一条件时停止，不得在本任务另造平台注册体系。

## 允许与禁止范围

允许修改：`webui/platforms.py` 中智联注册数据、`data/zhilian_city_codes.json`、浏览器账号与登录空间解析的专用模块、最小脱敏 `tests/fixtures/zhilian/`、`tests/test_platforms.py`、`tests/test_chrome_setup.py`。

禁止修改：智联列表/JD 抓取逻辑、数据库 migration、主链编排和 Vue。禁止保存 Cookie、完整页面、简历、JD、真实账号或本地绝对 profile 路径。

## 节点门禁 A：真实页面侦察

侦察前必须定义允许写入边界：只允许脱敏 fixture 和稳定映射；不自动投递、沟通或绕过验证。若没有当前可用的人工登录态，允许完成代码骨架和测试，但必须保持智联禁用，并把未核验项标为阻断。

- [ ] T201 核验当前智联公司性质稳定值/标签、全国 `jl0`、目标验收城市码、官方岗位 host/path 和元数据来源
- [ ] T202 核验列表、详情、登录墙、EdgeOne/验证码、明确空状态、限流和封禁页面的最小稳定 marker，只保存脱敏 fixture
- [ ] T203 记录每项外部事实的核验时间、fixture 版本和是否足以启用真实任务，不把历史参考升级成永久事实

## 节点门禁 B：发布 schema 与城市目录

首次写入稳定值前，逐项对照本轮证据。公司性质或目标城市码未确认时，不得写经验默认值；对应 fixture 保持缺失，并保证 `enabled_for_new_tasks=false`。

- [ ] T204 在 `webui/platforms.py` 注册智联显示名、schema 版本、公司性质字段、URL 规则、默认端口和启用状态
- [ ] T205 创建版本化 `data/zhilian_city_codes.json`，仅包含已确认规范城市映射并明确全国 `jl0`
- [ ] T206 实现 schema/城市 fixture 完整性检查：需选项字段非空、稳定值和标签非空、映射版本存在、缺城明确阻断
- [ ] T207 为 `/api/platforms`、`/api/filter-labels`、`/api/options` 所需服务投影补后端测试，不返回 profile 路径或路径摘要

## 节点门禁 C：登录空间

首次派生 profile 前，确认当前浏览器账号配置的真实结构和端口占用检测方式。必须能够证明同一账号的 BOSS 与智联 profile 不同，并能识别未知 profile；否则停止此节点。

- [ ] T208 实现 `<boss_profile_dir>.zhilian` 确定性派生、`zhilian:<account>` profile_key 和 9223 默认端口
- [ ] T209 保持 BOSS 9222/基础 profile 不变，实现同平台受控切换和未知 profile 占用拒绝
- [ ] T210 为账号 open/activate/delete 提供双平台登录空间检查函数：activate 只改草稿，delete 原子检查两个 profile、端口、run 锁和调优租约
- [ ] T211 在 `tests/test_chrome_setup.py` 覆盖 profile 隔离、端口隔离、未知占用、运行锁和不泄露路径

## 完成门禁

```powershell
uv run python -m unittest tests.test_platforms tests.test_chrome_setup tests.test_repo_hygiene
```

若所有启用所需外部事实均已核验，可让注册表按冻结规则启用；任一关键事实未核验，完成结论必须明确为“基础设施完成但智联新任务保持禁用”。检查 fixture 无敏感信息和完整页面内容后再提交。

## 解锁条件

只有公司性质、目标城市、列表/详情/空状态/阻断 marker 和登录空间全部有当前证据，且测试通过，才完整解锁 `tasks004.md`。若部分事实缺失，只解锁 adapter 骨架与已具 fixture 的测试，禁止真实启用。
