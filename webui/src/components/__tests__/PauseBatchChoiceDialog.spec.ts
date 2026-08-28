import { mount } from "@vue/test-utils";
import { nextTick } from "vue";
import PauseBatchChoiceDialog from "../PauseBatchChoiceDialog.vue";

describe("PauseBatchChoiceDialog（025 B076 批中暂停二选一，BaseDialog 外壳）", () => {
  afterEach(() => {
    document.body.innerHTML = "";
    document.body.classList.remove("dialog-open");
  });

  function mountDialog(props: Record<string, unknown> = {}) {
    return mount(PauseBatchChoiceDialog, {
      props: {
        open: false,
        batchInfo: null,
        ...props,
      },
      attachTo: document.body,
    });
  }

  // 真实时序：组件常驻挂载（open=false），打开靠 prop 翻转 → BaseDialog 的
  // 初始聚焦 watch 只在 open 变化时触发
  async function openDialog(batchInfo: { current: number; total: number } | null = null) {
    const wrapper = mountDialog({ batchInfo });
    await wrapper.setProps({ open: true });
    await nextTick();
    await nextTick();
    return wrapper;
  }

  it("渲染三按钮 + 当前批进度 + 平实提示文案", async () => {
    const wrapper = mountDialog({
      open: true,
      batchInfo: { current: 2, total: 4 },
    });
    await nextTick();
    expect(wrapper.get('[data-testid="pause-immediate"]').text()).toContain("立即停止");
    expect(wrapper.get('[data-testid="pause-graceful"]').text()).toContain("等这批抓完");
    expect(wrapper.get('[data-testid="pause-cancel"]').text()).toContain("取消");
    // 当前批进度（第几批/共几批）
    expect(wrapper.text()).toContain("第 2/4 批");
    // 平实提示文案，不渲染严重性/警告
    expect(wrapper.text()).toContain(
      "立即停止将放弃当前批次已抓取的内容，下次继续时需要重新抓取这一批",
    );
    expect(wrapper.text()).not.toContain("警告");
    expect(wrapper.text()).not.toContain("危险");
  });

  it("「立即停止」默认聚焦（回车可直接触发）", async () => {
    const wrapper = await openDialog({ current: 1, total: 2 });
    const btn = wrapper.get('[data-testid="pause-immediate"]').element as HTMLButtonElement;
    expect(document.activeElement).toBe(btn);
  });

  it("点击「立即停止」→ emit choose=immediate", async () => {
    const wrapper = mountDialog({ open: true, batchInfo: { current: 1, total: 2 } });
    await wrapper.get('[data-testid="pause-immediate"]').trigger("click");
    expect(wrapper.emitted("choose")?.[0]).toEqual(["immediate"]);
  });

  it("点击「等这批抓完」→ emit choose=graceful", async () => {
    const wrapper = mountDialog({ open: true, batchInfo: { current: 1, total: 2 } });
    await wrapper.get('[data-testid="pause-graceful"]').trigger("click");
    expect(wrapper.emitted("choose")?.[0]).toEqual(["graceful"]);
  });

  it("点击「取消」→ emit close（不暂停，任务继续跑）", async () => {
    const wrapper = mountDialog({ open: true, batchInfo: { current: 1, total: 2 } });
    await wrapper.get('[data-testid="pause-cancel"]').trigger("click");
    expect(wrapper.emitted("close")).toHaveLength(1);
    expect(wrapper.emitted("choose")).toBeUndefined();
  });

  it("Esc 关闭 → emit close", async () => {
    const wrapper = mountDialog({ open: true, batchInfo: { current: 1, total: 2 } });
    await nextTick();
    const panel = wrapper.get('[role="dialog"]');
    await panel.trigger("keydown", { key: "Escape" });
    expect(wrapper.emitted("close")).toHaveLength(1);
    expect(wrapper.emitted("choose")).toBeUndefined();
  });

  it("open=false 时不渲染面板（transition-stub 会带 fallthrough 属性，只认面板）", () => {
    const wrapper = mountDialog({ open: false });
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false);
    expect(document.querySelector(".dialog-backdrop")).toBeNull();
  });
});
