# Research: 大文件拆分重构（021）

## R1: app.py 巨型工厂的拆分策略

- **Decision**: 运行时上下文对象（PipelineContext）+ runner 逐个外迁。
- **Rationale**: 四个 runner 闭包共享状态引用密度极高（`_pipeline_tasks`×122、`store.`×354、`_pipeline_lock`×86、`_write_run_unless_finished`×39、`emit`×25），剪贴搬运必坏；显式对象一次性解开闭包耦合，之后各 runner 变成纯搬运。既有先例 `job_feedback_api` / `result_history_api`（service 模块 + register 薄路由）验证可行。
- **Alternatives**: 纯机械搬运（参数表过长、复用性差，用户否决）；本轮不拆 app.py（用户选择全量立项）。

## R2: 兼容面与测试爆炸半径

- **Decision**: 旧路径门面 re-export，保住测试 monkeypatch 面。
- **Rationale**: 体检确认测试对 `webui.app` 的 patch 集中在 `boss`、`_BossCdpSource`、`ai_service`、`ScraperExecutor` 及 6 个模块级助手；re-export 后测试零改动。
- **Alternatives**: 彻底改调用方（改动面大风险高，用户否决）。

## R3: store.py / source.py / boss_cdp_raw.py 拆分方式

- **Decision**: store 按域 mixin（先例 `store_migrations.py`）；source 按平台子模块 + 门面；boss_cdp_raw 按宪法 `scripts/boss/` 分组（CDPSession/异常族/详情抽取/城市映射/CLI）。
- **Rationale**: 全部有仓内先例可抄，属低智力搬运。
- **Alternatives**: 无（分组即宪法布局预定）。

## R4: DiscoveryView.vue 拆分方式

- **Decision**: 只抽 `<script setup>` 逻辑为 composables（先例 useScreenRoundFlow 等 4 个），模板段（~600 行，本身低于红线）不动。
- **Rationale**: 超标全在脚本（3141 行）；动模板风险最大且无必要。
- **Alternatives**: 拆子组件（需动模板，否决）。

## R5: 落位治理（本轮新增）

- **Decision**: 宪法 1.2.0 原则 VI——模块地图 + 75% 预警线（Py 600 / Vue 900）+ 门面禁改。
- **Rationale**: 用户提出"下一次 AI 怎么知道在哪做/是否开新文件"；需把落位规则固化为任何 AI 必读的硬规矩。
- **Alternatives**: 只写文档不改宪法（约束力弱，否决）。
