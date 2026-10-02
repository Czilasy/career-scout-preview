import { ref } from "vue";
import { useDiscoveryInstanceActions } from "../useDiscoveryInstanceActions";
import { useDiscoveryParallelFlow } from "../useDiscoveryParallelFlow";
import { useScreenRoundFlow } from "../useScreenRoundFlow";
import type { FlowProgressItem } from "../useDiscoveryFlowPresentation";

function setup() {
  const flow = ref<any>({ id: "flow-one", profile_id: "profile-one", selection: "all", tracks: [
    { id: "a", platform: "boss", status: "running", stage: "ai", scrape_run_id: "scrape-a", screen_run_id: "screen-a" },
    { id: "b", platform: "zhilian", status: "running", stage: "scrape", scrape_run_id: "scrape-b" },
  ] });
  const item = ref<FlowProgressItem>({ platform: "boss", trackId: "a", kind: "screen", stage: "screen", runId: "screen-a", finishRunId: "screen-a", status: "running", snapshot: { status: "running", progress: {}, logs: [] }, action: { kind: "pause", label: "暂停筛选" }, showFinishSave: true, showCancel: true, cancelLabel: "终止本轨", finishTestId: "finish-a", cancelTestId: "cancel-a", enteredAt: 0 });
  const parallel = { flow, stale: ref(false), refresh: vi.fn(async () => flow.value), operate: vi.fn(async () => flow.value) } as unknown as ReturnType<typeof useDiscoveryParallelFlow>;
  const requestInstanceAction = vi.fn(async (_kind, target) => target.execute("graceful"));
  const round = { requestInstanceAction } as unknown as ReturnType<typeof useScreenRoundFlow>;
  const finish = vi.fn(async (_id, options) => { options.instance.busy.value = true; await options.instance.onSaved({ ok: true }); options.instance.busy.value = false; });
  const refreshResults = vi.fn(async () => {});
  const notify = vi.fn();
  const actions = useDiscoveryInstanceActions({ parallel, round, finish, items: () => [item.value], refreshResults, notify });
  return { flow, item, parallel, round, finish, refreshResults, notify, actions };
}

it("046 A01: instance finish refreshes the common result with reading state preserved", async () => {
  const ctx = setup();
  await ctx.actions.finish("screen-a", "boss", "screen");
  expect(ctx.round.requestInstanceAction).toHaveBeenCalled();
  expect(ctx.finish).toHaveBeenCalledWith("screen-a", expect.objectContaining({ waitForBatch: true, instance: expect.any(Object) }));
  expect(ctx.parallel.refresh).toHaveBeenCalled();
  expect(ctx.refreshResults).toHaveBeenCalledExactlyOnceWith({ preservePresentation: true });
  expect(ctx.flow.value.tracks[1].status).toBe("running");
});

it("046 A04: pause forwards the clicked run and batch mode to the existing action path", async () => {
  const ctx = setup();
  await ctx.actions.operate("boss", "pause", "screen-a");
  expect(ctx.parallel.operate).toHaveBeenCalledWith("boss", "pause", { runId: "screen-a", mode: "graceful" });
});

it("046 A02: an old scrape click never operates the current AI", async () => {
  const ctx = setup();
  await ctx.actions.operate("boss", "pause-scrape", "scrape-a");
  await ctx.actions.finish("scrape-a", "boss", "scrape");
  expect(ctx.parallel.operate).not.toHaveBeenCalled();
  expect(ctx.finish).not.toHaveBeenCalled();
});

it("046: a delayed batch choice cannot finish a changed flow or run", async () => {
  const ctx = setup();
  let pending: any;
  ctx.round.requestInstanceAction = vi.fn(async (_kind, target) => { pending = target; });
  await ctx.actions.finish("screen-a", "boss", "screen");
  ctx.flow.value = { ...ctx.flow.value, id: "flow-new" };
  await pending.execute("immediate");
  expect(ctx.finish).not.toHaveBeenCalled();
});
