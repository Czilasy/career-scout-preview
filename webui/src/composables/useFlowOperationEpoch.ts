/**
 * 047 US5/C4：Flow 操作/响应代次与身份守卫。
 *
 * 每条 GET / action 响应在落地前都要核对「画像 + Flow + 轨道 + run」身份
 * 与单调代次：动作开始使此前的请求失效；另一轨道继续轮询，不以一个轨道
 * 的忙态冻结整个 Flow。旧响应被丢弃时返回 false，由调用方保持现状。
 *
 * 本模块不持有 durable 推进权：刷新/冷启动始终从权威响应恢复。
 */

export interface FlowOperationIdentity {
  profileId: string;
  flowId: string;
  platform?: string;
  runId?: string;
}

export interface FlowOperationEpoch {
  /** 开始一次新操作：使此前在途的 GET/action 响应失效。 */
  begin(identity: Partial<FlowOperationIdentity>): number;
  /** 取消此前全部请求（画像切换/新一轮）。 */
  invalidate(): void;
  /** 该响应是否仍代表当前身份与最新代次。 */
  isCurrent(identity: Partial<FlowOperationIdentity>, epoch: number): boolean;
  /** 当前代次快照（用于调用方在 await 前后比较）。 */
  current(): number;
  /** 当前身份快照。 */
  snapshot(): FlowOperationIdentity;
}

export function useFlowOperationEpoch(
  initial: Partial<FlowOperationIdentity> = {},
): FlowOperationEpoch {
  let epoch = 0;
  let bound = Boolean(initial.profileId || initial.flowId);
  let identity: FlowOperationIdentity = {
    profileId: initial.profileId || "", flowId: initial.flowId || "",
    platform: initial.platform || "", runId: initial.runId || "",
  };

  function matches(partial: Partial<FlowOperationIdentity>): boolean {
    if (partial.profileId !== undefined && partial.profileId !== identity.profileId) return false;
    if (partial.flowId !== undefined && partial.flowId !== identity.flowId) return false;
    // 轨道/run 只在调用方显式核对时比较：另一轨道的响应仍属同一 Flow。
    if (partial.platform !== undefined && partial.platform
        && identity.platform && partial.platform !== identity.platform) return false;
    if (partial.runId !== undefined && partial.runId
        && identity.runId && partial.runId !== identity.runId) return false;
    return true;
  }

  function begin(partial: Partial<FlowOperationIdentity>): number {
    epoch += 1;
    bound = true;
    if (partial.profileId !== undefined) identity.profileId = partial.profileId;
    if (partial.flowId !== undefined) identity.flowId = partial.flowId;
    if (partial.platform !== undefined) identity.platform = partial.platform;
    if (partial.runId !== undefined) identity.runId = partial.runId;
    return epoch;
  }

  function invalidate(): void {
    epoch += 1;
    bound = false;
    identity = { profileId: identity.profileId, flowId: "", platform: "", runId: "" };
  }

  return {
    begin,
    invalidate,
    isCurrent(partial, candidate) {
      return bound && candidate === epoch && matches(partial);
    },
    current: () => epoch,
    snapshot: () => ({ ...identity }),
  };
}
