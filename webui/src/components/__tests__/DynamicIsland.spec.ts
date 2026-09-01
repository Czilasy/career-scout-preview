// 036 B088 顶栏胶囊灵动岛组件测试：
// 四态渲染（idle/running/completed/attention）、点击派发目标、数字显示边界
// （total 未知省略分母 / pending=0 不显示）、数字变化可触发重渲染。
import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import DynamicIsland from "../DynamicIsland.vue";
import type { CapsuleStatusPayload, DynamicIslandState } from "../../composables/useDiscoveryState";

function makeStatus(
  capsule: DynamicIslandState,
  overrides: Partial<CapsuleStatusPayload> = {},
): CapsuleStatusPayload {
  return {
    platform: capsule.platform,
    phase: "judged",
    judged: 0,
    scope: capsule.platform,
    capsule,
    ...overrides,
  };
}

describe("DynamicIsland 四态渲染", () => {
  it("idle：显示平台名，低调常驻", () => {
    const wrapper = mount(DynamicIsland, {
      props: { status: makeStatus({ state: "idle", platform: "boss" }) },
    });
    expect(wrapper.get('[data-testid="dynamic-island-idle"]').text()).toContain("BOSS");
    expect(wrapper.get('[data-testid="dynamic-island-idle"]').text()).not.toContain("匹配");
  });

  it("idle 智联平台显示「智联」", () => {
    const wrapper = mount(DynamicIsland, {
      props: { status: makeStatus({ state: "idle", platform: "zhilian" }) },
    });
    expect(wrapper.get('[data-testid="dynamic-island-idle"]').text()).toContain("智联");
  });

  it("running：显示实时进度数字与呼吸点", () => {
    const wrapper = mount(DynamicIsland, {
      props: {
        status: makeStatus({
          state: "running",
          platform: "boss",
          progress: { phase: "scraping", done: 128, total: 300 },
        }),
      },
    });
    expect(wrapper.get('[data-testid="dynamic-island-running"]').text()).toContain("抓取 128/300");
    expect(wrapper.get('[data-testid="dynamic-island-running"]').find(".island-live").exists()).toBe(true);
  });

  it("running 筛选态显示筛选进度", () => {
    const wrapper = mount(DynamicIsland, {
      props: {
        status: makeStatus({
          state: "running",
          platform: "boss",
          progress: { phase: "screening", done: 45, total: 128 },
        }),
      },
    });
    expect(wrapper.get('[data-testid="dynamic-island-running"]').text()).toContain("筛选 45/128");
  });

  it("running total 未知省略分母（不显示假分母）", () => {
    const wrapper = mount(DynamicIsland, {
      props: {
        status: makeStatus({
          state: "running",
          platform: "boss",
          progress: { phase: "scraping", done: 12 },
        }),
      },
    });
    const text = wrapper.get('[data-testid="dynamic-island-running"]').text();
    expect(text).toContain("抓取 12");
    expect(text).not.toContain("/");
  });

  it("completed：显示匹配与待确认，待确认>0 标亮", () => {
    const wrapper = mount(DynamicIsland, {
      props: {
        status: makeStatus({
          state: "completed",
          platform: "boss",
          results: { matched: 5, pending: 2 },
        }),
      },
    });
    const el = wrapper.get('[data-testid="dynamic-island-completed"]');
    expect(el.text()).toContain("匹配 5");
    expect(el.text()).toContain("待确认 2");
    expect(el.find(".island-pending-dot").exists()).toBe(true);
  });

  it("completed 待确认为 0 时不显示待确认数字", () => {
    const wrapper = mount(DynamicIsland, {
      props: {
        status: makeStatus({
          state: "completed",
          platform: "boss",
          results: { matched: 5, pending: 0 },
        }),
      },
    });
    const el = wrapper.get('[data-testid="dynamic-island-completed"]');
    expect(el.text()).toContain("匹配 5");
    expect(el.text()).not.toContain("待确认");
    expect(el.find(".island-pending-dot").exists()).toBe(false);
  });

  it("attention：暂停显示橙色提醒文案", () => {
    const wrapper = mount(DynamicIsland, {
      props: {
        status: makeStatus({
          state: "attention",
          platform: "boss",
          attention: { kind: "paused", message: "任务已暂停，请处理后继续" },
        }),
      },
    });
    const el = wrapper.get('[data-testid="dynamic-island-attention"]');
    expect(el.text()).toContain("任务已暂停");
    expect(el.find(".attention-paused").exists()).toBe(true);
  });

  it("attention：出错显示红色提醒文案", () => {
    const wrapper = mount(DynamicIsland, {
      props: {
        status: makeStatus({
          state: "attention",
          platform: "boss",
          attention: { kind: "error", message: "任务执行出错" },
        }),
      },
    });
    const el = wrapper.get('[data-testid="dynamic-island-attention"]');
    expect(el.text()).toContain("任务执行出错");
    expect(el.find(".attention-error").exists()).toBe(true);
  });

  it("未筛选轮（phase=scraped）显示「待筛选 N」，与结果页 tab 一致", () => {
    // 端到端发现：未筛选轮岗位尚未 AI 筛选，不能显示"匹配"
    // （B038 + SC-009：胶囊与结果页看到的应是同一件事）。
    const wrapper = mount(DynamicIsland, {
      props: {
        status: makeStatus(
          { state: "completed", platform: "boss", results: { matched: 90, pending: 0 } },
          { phase: "scraped" },
        ),
      },
    });
    const el = wrapper.get('[data-testid="dynamic-island-completed"]');
    expect(el.text()).toContain("待筛选 90");
    expect(el.text()).not.toContain("匹配");
  });

  it("已筛选轮（phase=judged）仍显示「匹配 N」", () => {
    const wrapper = mount(DynamicIsland, {
      props: {
        status: makeStatus(
          { state: "completed", platform: "boss", results: { matched: 90, pending: 0 } },
          { phase: "judged" },
        ),
      },
    });
    expect(wrapper.get('[data-testid="dynamic-island-completed"]').text()).toContain("匹配 90");
  });

  it("status 为 null（未上抛）时回退 idle 默认平台", () => {
    const wrapper = mount(DynamicIsland, { props: { status: null } });
    expect(wrapper.find('[data-testid="dynamic-island-idle"]').exists()).toBe(true);
  });
});

describe("DynamicIsland 点击派发（FR-014~016 点击直达）", () => {
  it("idle 点击回主页", async () => {
    const wrapper = mount(DynamicIsland, {
      props: { status: makeStatus({ state: "idle", platform: "boss" }) },
    });
    await wrapper.get('[data-testid="dynamic-island-idle"]').trigger("click");
    expect(wrapper.emitted("navigate")?.[0]).toEqual(["home"]);
  });

  it("running 点击回任务", async () => {
    const wrapper = mount(DynamicIsland, {
      props: {
        status: makeStatus({
          state: "running", platform: "boss",
          progress: { phase: "scraping", done: 1, total: 10 },
        }),
      },
    });
    await wrapper.get('[data-testid="dynamic-island-running"]').trigger("click");
    expect(wrapper.emitted("navigate")?.[0]).toEqual(["task"]);
  });

  it("completed 点击去结果页", async () => {
    const wrapper = mount(DynamicIsland, {
      props: {
        status: makeStatus({
          state: "completed", platform: "boss",
          results: { matched: 3, pending: 1 },
        }),
      },
    });
    await wrapper.get('[data-testid="dynamic-island-completed"]').trigger("click");
    expect(wrapper.emitted("navigate")?.[0]).toEqual(["results"]);
  });

  it("attention 点击去处理现场", async () => {
    const wrapper = mount(DynamicIsland, {
      props: {
        status: makeStatus({
          state: "attention", platform: "boss",
          attention: { kind: "paused", message: "任务已暂停" },
        }),
      },
    });
    await wrapper.get('[data-testid="dynamic-island-attention"]').trigger("click");
    expect(wrapper.emitted("navigate")?.[0]).toEqual(["attention"]);
  });
});

describe("DynamicIsland 数字变化（FR-018 动画与重渲染）", () => {
  it("进度数字变化后文本随之更新", async () => {
    const wrapper = mount(DynamicIsland, {
      props: {
        status: makeStatus({
          state: "running", platform: "boss",
          progress: { phase: "scraping", done: 10, total: 100 },
        }),
      },
    });
    expect(wrapper.get('[data-testid="dynamic-island-running"]').text()).toContain("抓取 10/100");
    await wrapper.setProps({
      status: makeStatus({
        state: "running", platform: "boss",
        progress: { phase: "scraping", done: 42, total: 100 },
      }),
    });
    expect(wrapper.get('[data-testid="dynamic-island-running"]').text()).toContain("抓取 42/100");
  });
});
