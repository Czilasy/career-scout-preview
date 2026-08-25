import { mount } from "@vue/test-utils";
import PendingRecrawlCapsule from "../PendingRecrawlCapsule.vue";

function mountCapsule(props: Record<string, unknown> = {}) {
  return mount(PendingRecrawlCapsule, {
    props: { count: 3, ...props },
  });
}

describe("PendingRecrawlCapsule (B074)", () => {
  it("shows when there are pending jobs and count is above zero", () => {
    const wrapper = mountCapsule({ count: 3 });
    expect(wrapper.find('[data-testid="pending-recrawl-capsule"]').exists()).toBe(true);
  });

  it("hides on dismiss and does not reappear when count dips to zero then bounces back", async () => {
    const wrapper = mountCapsule({ count: 3 });
    expect(wrapper.find('[data-testid="pending-recrawl-capsule"]').exists()).toBe(true);

    // 受控语义：点「暂不处理」只上抛 dismiss 事件，父级共享状态翻转 dismissed prop
    await wrapper.get('[data-testid="pending-recrawl-dismiss"]').trigger("click");
    expect(wrapper.emitted("dismiss")).toHaveLength(1);
    await wrapper.setProps({ dismissed: true });
    expect(wrapper.find('[data-testid="pending-recrawl-capsule"]').exists()).toBe(false);

    // count 抖动归零再反弹：dismissed 隐藏态不得被 count 归零复位
    await wrapper.setProps({ count: 0 });
    expect(wrapper.find('[data-testid="pending-recrawl-capsule"]').exists()).toBe(false);
    await wrapper.setProps({ count: 2 });
    expect(wrapper.find('[data-testid="pending-recrawl-capsule"]').exists()).toBe(false);
  });

  it("reappears when the parent resets dismissed (resultEpoch change drives reset upstream)", async () => {
    const wrapper = mountCapsule({ count: 3, dismissed: true });
    expect(wrapper.find('[data-testid="pending-recrawl-capsule"]').exists()).toBe(false);

    await wrapper.setProps({ dismissed: false });
    expect(wrapper.find('[data-testid="pending-recrawl-capsule"]').exists()).toBe(true);
  });

  it("disappears directly when count reaches zero, with no green done badge", async () => {
    const wrapper = mountCapsule({ count: 3 });
    await wrapper.setProps({ count: 0 });
    expect(wrapper.find('[data-testid="pending-recrawl-capsule"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="pending-recrawl-done"]').exists()).toBe(false);
    expect(wrapper.text()).not.toContain("已全部处理");
  });

  it("emits recrawl from the action button", async () => {
    const wrapper = mountCapsule({ count: 3 });
    await wrapper.get('[data-testid="pending-recrawl"]').trigger("click");
    expect(wrapper.emitted("recrawl")).toHaveLength(1);
  });
});
