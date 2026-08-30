# 契约：前端 Discovery 依赖类型

**适用**：批次 8。`webui/src/composables/discoveryDeps.ts` 的公开形状（实施时以 5 个 composable 现有 `deps.X` 解构为完整成员清单，本文声明结构约定）。

## 结构

```ts
// 每域一个最小接口；成员 = 该 composable 现从 deps 解构的名字 + 类型
export interface WorkflowDeps { notify: NotifyFn; enterSearchStep: () => void; /* … */ }
export interface SearchDeps   { /* 8 成员，见 data-model E6 */ }
export interface ExecutionDeps{ /* 7 成员 */ }
export interface TasksDeps    { /* 7 成员 */ }
export interface ResultsDeps  { /* 7 成员 */ }

// 聚合：跨域调用经聚合对象显式解析，构造后回填改为显式 wiring 函数
export interface DiscoveryDeps extends WorkflowDeps, SearchDeps, ExecutionDeps, TasksDeps, ResultsDeps {}
export function wireDiscoveryDeps(pieces: DiscoveryPieces): DiscoveryDeps;
```

## 约定

1. **接口隔离**：每个 composable 只声明自己解构用到的成员；禁止 `deps: any = {}` 兜底签名（现状 5 处，全部清除）。
2. **wiring 显式化**：现状 `shared.notify = workflow.notify` 等 37 处构造后回填，改为 `wireDiscoveryDeps({...})` 一次成型；缺成员 = 编译错误。
3. **循环调用的类型可见**：跨域互相调用（如 execution 调 search.requireProfileConfirmed）经聚合类型可被 vue-tsc 检查——这是本契约的核心收益。
4. **行为冻结**：接口形状映射现状调用图，不改变任何调用时序与语义；样式与渲染像素级不变。
5. **测试跟随**：`webui/src/views/__tests__/DiscoveryView.spec.ts` 等 42 个 spec 的 fake deps 同步类型化；断言只随事实迁移不放松。
