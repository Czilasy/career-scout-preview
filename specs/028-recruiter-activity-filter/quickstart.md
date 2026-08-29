# Quickstart: 028 招聘者活跃时间筛选验证

## 前置

- 仓库根目录；Python 环境经 `uv`；前端构建需 `webui/` 下 npm。
- 无需真实账号/浏览器：全部验证走离线 fixture 与单测（Boss 值域来自 2026-08-29 实测，已固化为测试数据）。

## 聚焦测试（实现期间逐文件运行）

```bash
uv run python -m unittest tests.ai.test_recruiter_activity        # 判定域：映射/四档/未知/说明模板
uv run python -m unittest tests.source.test_recruiter_activity_capture  # 采集层：boss/zhilian/合并/落库
uv run python -m unittest tests.test_platforms                    # schema：新字段/版本/单选校验
uv run python -m unittest tests.ai.test_ai_match                  # 精筛接线：命中/未知不拦/不选不限
uv run python -m unittest tests.test_screen_flow                  # 续跑复用一致性
```

## 全量门禁（converge 执行）

```bash
uv run python -m unittest discover -s tests   # 后端全量
cd webui && npm test && npm run build         # 前端测试 + 构建
uv run python -m unittest tests.test_repo_hygiene  # 卫生检查
```

## 端到端手工场景（可选，离线）

1. 启动 WebUI，进入工作台 AI 筛选条件区：确认第 7 类「招聘者上次活跃」出现四档且默认全不选。
2. 点选「近一周」再点选「近三个月」：确认仅「近三个月」生效（单选替换）。
3. 用测试库（`.webui-state`）跑一次带第 7 类的筛选：确认无活跃数据的存量岗位全部通过第 7 类（结果 extra/说明可见「活跃时间未知」标注），带「半年前活跃」事实的岗位进入不匹配桶且说明为「负责人上次活跃半年前，超过要求的近三个月」。
4. 不选第 7 类再跑一次：确认任何岗位不因活跃被拦（与既有行为一致）。

## 预期结果

- 聚焦测试与全量门禁全绿；schema 版本为新值（BOSS=2、ZHILIAN=3）。
- 手工场景中单选交互与判定说明符合上表。
