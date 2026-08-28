# Research: 测试大文件拆分重构（027）

## R1: 子目录收集机制——空 `__init__.py` 而非依赖命名空间包递归

- **Decision**: 每个新子目录放一个空 `__init__.py`，使其成为常规包；`discover -s tests` 对含 `__init__.py` 的子目录递归收集是 unittest 跨版本稳定行为。
- **Rationale**: `tests/` 本身无 `__init__.py`（命名空间包），命名空间子目录的 discover 递归行为随 Python 版本演进、历史上有过缺陷修复；空 `__init__.py` 消除这一类不确定性。空文件不引入任何逻辑，符合纯搬运纪律。基线批（B1）以清单对账实证收集完整后才允许后续批次复制该结构。
- **Alternatives**: 依赖命名空间包隐式递归（版本敏感、无实证前不可押注，否决）；给 `tests/` 本身加 `__init__.py`（改变既有全部测试模块的导入身份，爆炸半径大，否决）。

## R2: 子目录命名与顶层包名撞名风险

- **Decision**: 子目录沿用被测域名：`webui_app`、`healthy_pipeline`、`tuning`、`source`、`ai`、`webui_store`、`chrome_setup`。
- **Rationale**: discover 以 `tests/` 为 top_level_dir 时会把子目录包名作为顶层可导入名（`ai`、`source`、`tuning` 等）。已核验：仓库无同名顶层模块，产品代码 import 均带 `webui.`/`scripts.` 前缀；测试运行期 sys.path 上的第三方包无同名常用包。B0 基线批以「全量收集数不变 + 全绿」做最终实证；若实证发现撞名（表现为收集数变化或 import 报错），改用带前缀命名（如 `tests_ai`）并在该批记录。
- **Alternatives**: 一律加前缀规避（名字变丑且无实证依据表明必要，作为后备不首选）。

## R3: 基线快照与对账手段

- **Decision**: 快照 = 用例总数 + 全部「类名.方法名」条目的**含重复完整列表**（排序后落盘）；对账 = 排序列表逐行 diff。快照文件存系统临时目录（`$env:TEMP` / `$TEMP`），不进仓库。
- **Rationale**: 模块路径在搬运后必然变化，故条目去掉模块前缀；含重复列表同时捕获「漏搬」（条目缺失）与「重复收集」（条目多出）两类事故，去重集合两者都抓不到。临时脚本用完即弃，不新增仓库工具文件（行数核对与清单核对均为一次性命令）。
- **Alternatives**: 只比总数（抓不到等量换名/错搬，否决）；提交一个永久清单脚本进仓库（超出本 Spec 范围、增加维护面，否决——未来如常做再立项）。

## R4: 共享帮手落位——域内共享模块，单一使用者随类搬

- **Decision**: 横跨多个拆分文件共用的符号抽到域内共享模块：`tests/healthy_pipeline/harness.py`（6 个函数）、`tests/tuning/builders.py`（5 个符号）；仅供单一类使用者（`test_webui_app.py` 的 `_tuning_quality_context`、`_make_valid_manifest_payload_web`，实测各 1 处使用类）随使用类整组搬迁，不集中。
- **Rationale**: 实测无任何跨 7 文件的符号共享（反向依赖为零），故不需要顶层公共模块；域内落位让每个子目录自包含。共享模块以非 `test` 前缀命名，不被收集。抽离动作与拆分同批完成（先建共享模块、再搬域文件），避免产生「仅抽离未拆分」的中间形态。
- **Alternatives**: 顶层 `tests/shared_harness.py` 大一统（无跨域共享事实，纯增层级，否决）；每个拆分批各自重复一份帮手（违背纯搬运且制造分叉，否决）。

## R5: 批次顺序——快赢先行、机制验证批打头

- **Decision**: B0 基线 → B1 chrome_setup（最小、最自包含）→ B2 webui_store → B3 source → B4 ai → B5 tuning → B6 healthy_pipeline → B7 webui_app → B8 终检。
- **Rationale**: 沿用 021「快赢先行、由小到大积累搬运经验」；B1 兼任机制验证（子目录收集、聚焦命令、对账全链路首跑），机制有问题在最小批次暴露。三个巨型文件放最后，此时流程已被 4 个批次验证过。
- **Alternatives**: 先攻最大文件（机制未验证就上最大爆炸半径，否决）。

## R6: 聚焦测试命令形态

- **Decision**: 聚焦口径为 `uv run python -m unittest discover -s tests/<子目录>`（子目录有 `__init__.py`，discover 可定位包）；B1 实证后固化进 quickstart。若实证不通，退化为「直接跑受影响域相关的全量」，门禁强度不降。
- **Rationale**: 每批全量是硬门禁，聚焦命令用于快速反馈、缩短批内迭代循环。
- **Alternatives**: 只跑全量（反馈慢，批内每次微调都要等全量，否决为唯一手段）。

## R7: 宪法与模块地图不动

- **Decision**: 本 Spec 不修订宪法、不登记模块地图。
- **Rationale**: 宪法原则 II 的 800 行红线针对业务文件，测试文件用本 Spec 的 ≤2000 门禁（用户拍板），属 Spec 级规则而非宪法级；模块地图覆盖 `webui/`、`scripts/` 业务域，测试子目录不在其范围。原则 V 的前端门禁豁免为一次性用户裁决，记录在 spec FR-005 与 plan 宪法检查表，不入宪法正文。
- **Alternatives**: 宪法加「测试文件 ≤2000」条款（一次重构不值得升宪法版本，且未来测试尺寸策略可能随拆分完成再议，否决）。
