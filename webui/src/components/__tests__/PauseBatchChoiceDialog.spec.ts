import { mount } from "@vue/test-utils";
import { nextTick } from "vue";
import PauseBatchChoiceDialog from "../PauseBatchChoiceDialog.vue";

describe("PauseBatchChoiceDialog（025 B076 批中二选一，迷你档）", () => {
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

  it("迷你档：两个小键 + 一行说明（批次写进说明行）+ 平实文案", async () => {
    const wrapper = mountDialog({
      open: true,
      batchInfo: { current: 2, total: 4 },
    });
    await nextTick();
    // 迷你宽度档（不是默认 620 大框）
    expect(wrapper.get('[role="dialog"]').classes()).toContain("dialog-xs");
    expect(wrapper.get('[data-testid="pause-immediate"]').text()).toContain("立即停止");
    expect(wrapper.get('[data-testid="pause-graceful"]').text()).toContain("等这批抓完");
    // 批次 + 后果写成一行，不单独占一块
    expect(wrapper.text()).toContain("第 2 批 / 共 4 批");
    expect(wrapper.text()).toContain("现在停，这一批要重抓");
    // 没有底部按钮条：取消 = 右上 ✕ / Esc
    expect(wrapper.find('[data-testid="pause-cancel"]').exists()).toBe(false);
    expect(wrapper.find(".dialog-header .icon-button").exists()).toBe(true);
    // 配色身份：稳妥项淡主色底、有损失项中性底 + 危险色文字（不给主色实底）
    expect(wrapper.get('[data-testid="pause-graceful"]').classes()).toContain("is-graceful");
    expect(wrapper.get('[data-testid="pause-immediate"]').classes()).toContain("is-immediate");
    // 平实文案：不出现严重性字样
    expect(wrapper.text()).not.toContain("警告");
    expect(wrapper.text()).not.toContain("危险");
  });

  it("只有一批时说明行写「当前只有这一批」", async () => {
    const wrapper = mountDialog({ open: true, batchInfo: { current: 1, total: 1 } });
    await nextTick();
    expect(wrapper.text()).toContain("当前只有这一批");
  });

  it("默认聚焦右上 ✕：回车等于取消，不会误伤这一批", async () => {
    const wrapper = await openDialog({ current: 1, total: 2 });
    const close = wrapper.get(".dialog-header .icon-button").element as HTMLButtonElement;
    expect(document.activeElement).toBe(close);
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

  it("点右上 ✕ → emit close（不暂停，任务继续跑）", async () => {
    const wrapper = mountDialog({ open: true, batchInfo: { current: 1, total: 2 } });
    await wrapper.get(".dialog-header .icon-button").trigger("click");
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

  it("kind=finish（结束并保存）：文案切换，迷你档一致", async () => {
    const wrapper = mountDialog({
      open: false,
      kind: "finish",
      batchInfo: { current: 2, total: 4 },
    });
    await wrapper.setProps({ open: true });
    await nextTick();
    await nextTick();
    expect(wrapper.text()).toContain("结束并保存结果");
    expect(wrapper.get('[data-testid="pause-graceful"]').text()).toContain("等这批抓完再保存");
    expect(wrapper.get('[data-testid="pause-immediate"]').text()).toContain("立即保存");
    expect(wrapper.text()).toContain("立即保存只存已落盘的");
    const close = wrapper.get(".dialog-header .icon-button").element as HTMLButtonElement;
    expect(document.activeElement).toBe(close);
  });

  it("kind 缺省仍是暂停文案（旧调用不受影响）", async () => {
    const wrapper = mountDialog({ open: true, batchInfo: { current: 1, total: 2 } });
    await nextTick();
    expect(wrapper.text()).toContain("暂停 AI 筛选");
    expect(wrapper.get('[data-testid="pause-immediate"]').text()).toContain("立即停止");
  });

  it("文案可用 props 覆盖（description 覆盖时不再拼批次）", async () => {
    const wrapper = mountDialog({
      open: true,
      kind: "finish",
      batchInfo: { current: 2, total: 4 },
      title: "自定义标题",
      description: "自定义说明",
      immediateLabel: "马上存",
    });
    await nextTick();
    expect(wrapper.text()).toContain("自定义标题");
    expect(wrapper.text()).toContain("自定义说明");
    expect(wrapper.text()).not.toContain("第 2 批 / 共 4 批");
    expect(wrapper.get('[data-testid="pause-immediate"]').text()).toContain("马上存");
  });

  describe("045 v2 关闭确认场景（kind=close）", () => {
    it("只给一个动作键，不出现等待类选项", async () => {
      const wrapper = mountDialog({ open: true, kind: "close" });
      await nextTick();
      expect(wrapper.find('[data-testid="pause-immediate"]').exists()).toBe(true);
      expect(wrapper.get('[data-testid="pause-immediate"]').text()).toContain("立即结束");
      expect(wrapper.find('[data-testid="pause-graceful"]').exists()).toBe(false);
    });

    it("说明任务正在进行中，并告知可先暂停", async () => {
      const wrapper = mountDialog({ open: true, kind: "close" });
      await nextTick();
      const text = wrapper.text();
      expect(text).toContain("还有任务正在进行中");
      expect(text).toContain("这一批会丢弃");
      expect(text).toContain("先暂停");
    });

    it("等待中动作键禁用并出现旋转指示", async () => {
      const wrapper = mountDialog({ open: true, kind: "close", busy: true });
      await nextTick();
      const button = wrapper.get('[data-testid="pause-immediate"]');
      expect(button.attributes("disabled")).toBeDefined();
      expect(wrapper.find(".spin").exists()).toBe(true);
    });

    it("等待中仍可取消：右上关闭键保持可用", async () => {
      const wrapper = mountDialog({ open: true, kind: "close", busy: true });
      await nextTick();
      const closeButton = wrapper.get(".dialog-header .icon-button");
      expect(closeButton.attributes("disabled")).toBeUndefined();
    });

    it("选立即结束会发出 immediate", async () => {
      const wrapper = mountDialog({ open: true, kind: "close" });
      await nextTick();
      await wrapper.get('[data-testid="pause-immediate"]').trigger("click");
      expect(wrapper.emitted("choose")?.[0]).toEqual(["immediate"]);
    });

    it("等待中点击动作键不再发出事件", async () => {
      const wrapper = mountDialog({ open: true, kind: "close", busy: true });
      await nextTick();
      await wrapper.get('[data-testid="pause-immediate"]').trigger("click");
      expect(wrapper.emitted("choose")).toBeUndefined();
    });
  });

  describe("045 v2 未运行关窗场景（kind=close_save）", () => {
    it("沿用 v1 口径：结束并保存 / 取消，无等待类选项", async () => {
      const wrapper = mountDialog({ open: true, kind: "close_save" });
      await nextTick();
      expect(wrapper.get('[data-testid="pause-immediate"]').text()).toContain("结束并保存");
      expect(wrapper.find('[data-testid="pause-graceful"]').exists()).toBe(false);
      expect(wrapper.text()).toContain("结束并保存结果");
      expect(wrapper.text()).toContain("进历史");
    });

    it("无损动作走稳妥色，而不是危险色", async () => {
      const wrapper = mountDialog({ open: true, kind: "close_save" });
      await nextTick();
      expect(wrapper.get('[data-testid="pause-immediate"]').classes()).toContain("is-graceful");
      expect(wrapper.get('[data-testid="pause-immediate"]').classes()).not.toContain("is-immediate");
    });
  });
});
