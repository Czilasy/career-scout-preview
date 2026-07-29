import { mount } from "@vue/test-utils";
import TuningWorkspace from "../TuningWorkspace.vue";
import type { TuningExperiment } from "../../types";

function experiment(overrides: Partial<TuningExperiment> = {}): TuningExperiment {
  return {
    id: "exp-1",
    status: "running",
    spec_version: "011-deep-configuration-probing",
    current_stage: "detail",
    current_candidate_id: "candidate-2",
    current_round_id: "round-7",
    progress: {
      confirmed_rounds: 12,
      invalid_rounds: 1,
      remaining_required_rounds: 18,
      estimated_remaining_seconds: 8200,
    },
    evidence: [{ id: "round-6", status: "confirmed", total_duration_ms: 12000 }],
    can_cancel: true,
    can_resume: false,
    can_apply: false,
    ...overrides,
  };
}

describe("TuningWorkspace", () => {
  it("renders an actionable empty state", async () => {
    const wrapper = mount(TuningWorkspace, { props: { experiment: null } });
    expect(wrapper.get('[data-testid="tuning-empty"]').text()).toContain("尚未创建");
    await wrapper.get('[data-testid="create-tuning"]').trigger("click");
    expect(wrapper.emitted("create")).toHaveLength(1);
  });

  it("renders loading without stale controls", () => {
    const wrapper = mount(TuningWorkspace, {
      props: { experiment: experiment(), loading: true },
    });
    expect(wrapper.get('[data-testid="tuning-loading"]').text()).toContain("读取");
    expect(wrapper.find('[data-testid="apply-tuning"]').exists()).toBe(false);
  });

  it("shows serial progress, candidate, round and remaining estimate", () => {
    const wrapper = mount(TuningWorkspace, { props: { experiment: experiment() } });
    expect(wrapper.get('[data-testid="tuning-stage"]').text()).toContain("detail");
    expect(wrapper.get('[data-testid="tuning-candidate"]').text()).toContain("candidate-2");
    expect(wrapper.get('[data-testid="tuning-round"]').text()).toContain("round-7");
    expect(wrapper.get('[data-testid="tuning-progress"]').text()).toContain("12");
    expect(wrapper.get('[data-testid="tuning-remaining"]').text()).toContain("2小时");
    expect(wrapper.find('[data-testid="apply-tuning"]').exists()).toBe(false);
  });

  it("shows a blocker and emits resume without inventing recovery", async () => {
    const wrapper = mount(TuningWorkspace, {
      props: {
        experiment: experiment({
          status: "blocked",
          blocked_code: "source_blocked",
          blocked_reason: "来源要求人工验证",
          can_resume: true,
        }),
      },
    });
    expect(wrapper.get('[data-testid="tuning-block"]').text()).toContain("来源要求人工验证");
    await wrapper.get('[data-testid="resume-tuning"]').trigger("click");
    expect(wrapper.emitted("resume")).toHaveLength(1);
  });

  it("expands evidence and gates apply to completed experiments", async () => {
    const incomplete = mount(TuningWorkspace, {
      props: { experiment: experiment({ status: "evaluating", can_apply: false }) },
    });
    expect(incomplete.find('[data-testid="apply-tuning"]').exists()).toBe(false);

    const completed = mount(TuningWorkspace, {
      props: { experiment: experiment({ status: "completed", can_apply: true, can_cancel: false }) },
    });
    expect(completed.get("details").text()).toContain("round-6");
    await completed.get('[data-testid="apply-tuning"]').trigger("click");
    expect(completed.emitted("apply")).toHaveLength(1);
  });

  it("offers rollback only when a previous complete version exists", async () => {
    const wrapper = mount(TuningWorkspace, {
      props: {
        experiment: experiment({ status: "completed", can_apply: true, can_cancel: false }),
        canRollback: true,
      },
    });
    await wrapper.get('[data-testid="rollback-tuning"]').trigger("click");
    expect(wrapper.emitted("rollback")).toHaveLength(1);
  });
});
