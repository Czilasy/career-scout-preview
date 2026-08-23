# Specification Quality Checklist: 跨平台岗位去重（BOSS+智联）

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-08-23
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] No implementation details (languages, frameworks, APIs)
- [x] Focused on user value and business needs
- [x] Written for non-technical stakeholders
- [x] All mandatory sections completed

## Requirement Completeness

- [x] No [NEEDS CLARIFICATION] markers remain（质询未应答项已按推荐项代定并写入 Assumptions，声明可翻案）
- [x] Requirements are testable and unambiguous
- [x] Success criteria are measurable
- [x] Success criteria are technology-agnostic (no implementation details)
- [x] All acceptance scenarios are defined
- [x] Edge cases are identified
- [x] Scope is clearly bounded
- [x] Dependencies and assumptions identified

## Feature Readiness

- [x] All functional requirements have clear acceptance criteria
- [x] User scenarios cover primary flows
- [x] Feature meets measurable outcomes defined in Success Criteria
- [x] No implementation details leak into specification

## Notes

- 初稿按「单 run 内跨平台合并」假设编写；plan 前调查确认流水线按平台各跑一条、两平台仅在前端合并视图汇合，spec 已按真实架构重写（生效点 = 后跑平台筛选输入组装处，先跑平台轮次不可回改）。
- 2026-08-23 用户确认修订一：比对基准由「对端最新一轮」改为「对端近 30 天全部可见轮」（交替跑 + 历史轮多时仅比最新轮漏判已筛岗位；显示层无回归分析见 research.md R2）。
- 2026-08-23 用户确认修订二：① 重复岗位**成组展示**（列表一行 + 详情两平台副本并排，含薪资对比），取代「徽标 + 剔除列表角落」的弱可见方案；② 新增 US5 可见性要求（进度报数、完成口径对账、任务事件台账、去重开关）——用户明确强调静默后台剔除的错误漏岗风险。
- 指纹口径、薪资/发布时间容忍度、画像过滤方式、30 天窗常量为代定决策，已在 Assumptions 显式声明；进入实现前用户复核可翻案。
- 「仅抓取轮不去重」「重抓/补筛不触发判定」「旧数据不回溯」「同平台跨轮去重留给 B055」「按岗位类型归堆另立 spec」为明确边界，防止范围膨胀。
