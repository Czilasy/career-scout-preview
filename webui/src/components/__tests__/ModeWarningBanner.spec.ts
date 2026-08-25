import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";

import ModeWarningBanner from "../ModeWarningBanner.vue";

function mountBanner(props: { extremeWarning: boolean; largeTaskWarning: boolean }) {
  return mount(ModeWarningBanner, { props });
}

describe("ModeWarningBanner（024 黄色警示区）", () => {
  it("极限档只显示极限警告", () => {
    const wrapper = mountBanner({ extremeWarning: true, largeTaskWarning: false });
    expect(wrapper.find('[data-testid="mode-warning-banner"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="extreme-warning"]').text()).toBe("极高概率限流封号");
    expect(wrapper.find('[data-testid="large-task-warning"]').exists()).toBe(false);
  });

  it("大任务只显示规模警告（任何档位）", () => {
    const wrapper = mountBanner({ extremeWarning: false, largeTaskWarning: true });
    expect(wrapper.find('[data-testid="mode-warning-banner"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="large-task-warning"]').text()).toBe("任务规模过大可能封号");
    expect(wrapper.find('[data-testid="extreme-warning"]').exists()).toBe(false);
  });

  it("极限档 + 大任务同时满足时两条警告同时显示", () => {
    const wrapper = mountBanner({ extremeWarning: true, largeTaskWarning: true });
    expect(wrapper.find('[data-testid="extreme-warning"]').text()).toBe("极高概率限流封号");
    expect(wrapper.find('[data-testid="large-task-warning"]').text()).toBe("任务规模过大可能封号");
  });

  it("都不满足时整体隐藏", () => {
    const wrapper = mountBanner({ extremeWarning: false, largeTaskWarning: false });
    expect(wrapper.find('[data-testid="mode-warning-banner"]').exists()).toBe(false);
  });

  it("警示区固定显示、无关闭按钮/叉号", () => {
    const wrapper = mountBanner({ extremeWarning: true, largeTaskWarning: true });
    expect(wrapper.find("button").exists()).toBe(false);
    expect(wrapper.find('[aria-label*="关闭" i]').exists()).toBe(false);
  });
});
