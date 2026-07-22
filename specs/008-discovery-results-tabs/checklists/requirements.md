# Specification Quality Checklist: 发现结果页三 tab 分类与单岗位 JD 补抓

**Purpose**：在进入 planning 阶段前，校验 spec 的完整性与质量。  
**Created**：2026-07-22  
**Feature**：[spec.md](../spec.md)

## Content Quality

- [x] 无实现细节（语言、框架、API 名）
- [x] 聚焦用户价值与业务诉求
- [x] 面向非技术干系人可读
- [x] 所有必填章节已完成

## Requirement Completeness

- [x] 不残留 [NEEDS CLARIFICATION] 标记
- [x] 需求可测试、无歧义
- [x] 成功标准可度量
- [x] 成功标准与技术无关（无实现细节）
- [x] 所有验收场景已定义
- [x] 边界情况已识别（JD 抓取失败、补抓失败、tab 切换不跳转、持久化策略差异）
- [x] 范围边界清晰（FR-013 明确排除项）
- [x] 依赖与假设已识别

## Feature Readiness

- [x] 所有功能需求均有明确验收条件
- [x] 用户场景覆盖主流程（匹配/不匹配/已筛除浏览、补抓、感兴趣/不感兴趣标记、查看详情、tab 切换、冗余清除）
- [x] 功能满足成功标准中定义的可度量结果
- [x] 规格中无实现细节泄漏

## Notes

- 本规格与 006 FR-006 存在语义冲突：006 将「不感兴趣」做持久化，008 改为本轮内存级。已在 FR-011 和「与既有规格的关系」中显式声明替代关系，plan 阶段需处理 006 已落地的持久化代码改造。
- 单岗位 JD 补抓端点（含 JD 回写后端结果数据）留待 plan 阶段定义契约：复用现有 `scrape_details` 还是新增按岗位 ID 触发的端点 + JD 字段更新端点。
- 结果 API 是否已返回 matched / unmatched / dropped 分类字段与 JD 抓取状态字段，需在 plan 阶段的 research 步骤中确认；若字段缺失，需在 data-model 中补齐。
- 后端调整范围（plan 阶段细化）：① 不感兴趣持久化移除；② 补抓 JD 回写端点 + JD 字段更新；③ 已筛除移除理由持久化到结果数据（若 007 未落盘）；④ 分类字段补齐（若缺失）。
