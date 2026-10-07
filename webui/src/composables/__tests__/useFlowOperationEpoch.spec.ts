import { describe, expect, it } from "vitest";
import { useFlowOperationEpoch } from "../useFlowOperationEpoch";

describe("useFlowOperationEpoch", () => {
  it("invalidates the earlier in-flight response when an action starts", () => {
    const guard = useFlowOperationEpoch();
    const readEpoch = guard.begin({ profileId: "p1", flowId: "f1" });
    // 动作开始：旧 GET 立即失效
    guard.begin({ profileId: "p1", flowId: "f1", platform: "boss", runId: "r1" });
    expect(guard.isCurrent({ profileId: "p1", flowId: "f1" }, readEpoch)).toBe(false);
    expect(guard.isCurrent({ profileId: "p1", flowId: "f1" }, guard.current())).toBe(true);
  });

  it("rejects a response after the profile/flow changed", () => {
    const guard = useFlowOperationEpoch();
    const epoch = guard.begin({ profileId: "p1", flowId: "f1" });
    guard.begin({ profileId: "p2", flowId: "f2" });
    expect(guard.isCurrent({ profileId: "p1", flowId: "f1" }, epoch)).toBe(false);
    // 另一轨道的响应仍属同一 Flow
    const bossEpoch = guard.begin({ profileId: "p2", flowId: "f2", platform: "boss" });
    guard.begin({ profileId: "p2", flowId: "f2", platform: "boss" });
    expect(guard.isCurrent({ profileId: "p2", flowId: "f2", platform: "boss" }, bossEpoch)).toBe(false);
    expect(guard.isCurrent({ profileId: "p2", flowId: "f2" }, guard.current())).toBe(true);
  });

  it("keeps only one winner when start operations overlap", () => {
    const guard = useFlowOperationEpoch();
    const first = guard.begin({ profileId: "p1", flowId: "f1" });
    const second = guard.begin({ profileId: "p1", flowId: "f1" });
    expect(guard.isCurrent({ profileId: "p1", flowId: "f1" }, first)).toBe(false);
    expect(guard.isCurrent({ profileId: "p1", flowId: "f1" }, second)).toBe(true);
  });

  it("drops everything after invalidate until a new identity is set", () => {
    const guard = useFlowOperationEpoch();
    const epoch = guard.begin({ profileId: "p1", flowId: "f1" });
    guard.invalidate();
    expect(guard.isCurrent({ profileId: "p1", flowId: "f1" }, epoch)).toBe(false);
    expect(guard.isCurrent({}, guard.current())).toBe(false);
    const next = guard.begin({ profileId: "p1", flowId: "f9" });
    expect(guard.isCurrent({ profileId: "p1", flowId: "f9" }, next)).toBe(true);
  });

  it("does not compare platform/run unless the caller asks", () => {
    const guard = useFlowOperationEpoch();
    const epoch = guard.begin({ profileId: "p1", flowId: "f1", platform: "boss" });
    // 不传 platform 的兄弟轨道响应仍然有效
    expect(guard.isCurrent({ profileId: "p1", flowId: "f1" }, epoch)).toBe(true);
  });
});
