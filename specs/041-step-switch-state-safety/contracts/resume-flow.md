# Contract: 简历分析后台异步流程（Resume Analysis Flow）

**Spec**: `specs/041-step-switch-state-safety/spec.md` | **Module**: `useResumeAnalysisFlow.ts`

## 用途

简历上传点分析后不锁用户：后台异步分析，分析中用户可点灵动岛/翻历史/切页；完成按真实进度落点（完→02、没完→01 显示分析中）；失败按真实失败展示不误报。

## 公开接口

```typescript
interface ResumeAnalysisInput {
  file: File;
  platform: Platform;
  aiConsent: boolean;
}

type ResumeAnalysisPhase = "idle" | "analyzing" | "succeeded" | "failed";

export function useResumeAnalysisFlow(deps: {
  refs: {
    activeStep: Ref<string>;
    uploadBusy: Ref<boolean>;
    resumeError: Ref<string>;
    resumeAnalysis: Ref<unknown>;
  };
  api: {
    postAnalyzeResume: (form: FormData) => Promise<unknown>;
    cancelActiveTasksForNewRound: () => Promise<boolean>;
    clearLatestResult: () => Promise<boolean>;
    enterSearchStep: () => void;
    notify: (msg: string, tone?: Notice["tone"]) => void;
  };
}): {
  /** 启动后台分析（不 await 阻塞 UI） */
  startAnalysis(input: ResumeAnalysisInput): void;
  /** 当前分析态（供灵动岛 analyzing 派生） */
  phase: ComputedRef<ResumeAnalysisPhase>;
  /** 用户回当前流程时按真实进度落点（spec FR-019） */
  landOnReturn(): void;
  /** 分析完成回调：自动进 02（仅当用户在 01 或主动回当前流程，spec FR-020） */
  onAnalysisComplete(): void;
};
```

## 行为契约

1. **不锁用户**（spec FR-018）：`startAnalysis` 不 await 阻塞 UI；分析中 `uploadBusy` 仅禁上传按钮，用户可点灵动岛/翻历史/切页。
2. **后台照跑**（spec FR-018）：分析请求在后台进行，不被用户位置打断。
3. **按真实进度落点**（spec FR-019）：用户回当前流程（主动切回或点「回到最新」）：succeeded → 02；analyzing → 01 显示分析中；failed → 01 显示真实失败。
4. **完成自动进 02**（spec FR-020）：分析完成时若用户在 01 或主动回当前流程，自动 enterSearchStep（切 02）；用户已离开则不强制切（等用户回来按进度落点）。
5. **灵动岛分析中态**（spec FR-021）：`phase=analyzing` 时灵动岛显示「分析中」，点击回 01。
6. **不误报**（spec FR-022）：失败展示真实失败原因（来自 API error），不出现「浏览器找不到/刷新失败」等与真实进度不一致的误报；分析中及失败态与真实状态一致。

## 调用方

- `useDiscoverySearch.ts` `analyzeResume`（L245-313）：改调 `startAnalysis`，移除同步 await 与强制 enterSearchStep。
- `DynamicIsland.vue` / `useIslandCarousel.ts`：`phase=analyzing` 派生 analyzing 态（`IslandPhase` 增 analyzing）。
- `useDiscoveryResults.ts` `returnToLatest`：用户点「回到最新」时调 `landOnReturn`。
- `useDiscoveryExecution.ts` `restoreRunningTask`：恢复时若检测到分析中任务，接 `phase`。

## 不变式

- `startAnalysis` 调用后立即返回（不阻塞）。
- 分析中用户离开后回来，落点由 `phase` 与当前位置共同决定，不强制切页。
- 失败 error 文案来自 API 真实响应，不编造「浏览器找不到」。
- 不新增纯 pass 吞异常（宪法原则 VII）：分析失败留痕或显式返回 error。
