# Specification Quality Checklist: 多账号轮询分摊抓取白箱接入（B091 V2）

**Purpose**: 检查 038 V2 是否在保留原有多账号主体的同时，完整定义白箱接入、证据边界和验收范围。
**Created**: 2026-09-04
**Feature**: [../spec.md](../spec.md)

## Content Quality

- [x] 没有把具体代码实现写成用户需求；实现落位保留在 Plan。
- [x] 需求聚焦 038 多账号轮询的可追溯价值。
- [x] 用户和维护者可以根据需求判断是否达成，不要求理解内部实现。
- [x] Spec 必要章节完整，且 V2 明确继承 V1。

## Requirement Completeness

- [x] 没有遗留 `[NEEDS CLARIFICATION]`。
- [x] 白箱快照、分配、切换、接管、终态和安全边界均可测试。
- [x] Success Criteria 包含任务查看、轮换核对、终态一致性和敏感信息排除。
- [x] 验收场景覆盖正常轮换、撞墙接管、全撞暂停和已结束任务查看。
- [x] Edge Cases 覆盖白箱缺失、写入失败、任务结束后查看和批处理粒度。
- [x] 范围明确：不新增独立白箱产品，不改变 038 主流程语义。
- [x] 依赖与假设已写明：复用现有任务事件和日志查看能力。

## Feature Readiness

- [x] 所有白箱功能要求都有对应验收场景或 Success Criteria。
- [x] User Story 5 可独立通过聚焦测试和小规模真实 E2E 验证。
- [x] V2 的 Plan 与 Tasks 已标记白箱接入的文件边界和验证门禁。
- [x] V1 与 V2 的变化已在 `changes.md` 记录。

## Notes

- V2 当前仍是 Draft；Plan/Tasks 已按候选设计形成，待用户确认后进入实现阶段。
- 白箱接入属于 038 主体；只有未来被多个主体独立复用时，才重新评估是否抽取系统级 Spec。
