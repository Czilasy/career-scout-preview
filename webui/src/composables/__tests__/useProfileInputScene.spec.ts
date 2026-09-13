import { mount } from "@vue/test-utils";
import { defineComponent, h } from "vue";
import { beforeEach, describe, expect, it } from "vitest";

import { useDiscoverySceneState } from "../useDiscoverySceneState";
import { useDiscoverySceneIdentity } from "../useDiscoverySceneIdentity";
import { useProfileInputScene } from "../useProfileInputScene";
import { useDiscoveryState } from "../useDiscoveryState";

function harness(profileId: string) {
  const state = useDiscoveryState({ profileId }, () => {});
  const { identity } = useDiscoverySceneIdentity(state, { profileId });
  const Harness = defineComponent({
    setup() {
      useProfileInputScene(state, identity);
      return () => h("textarea", { ref: state.profileInputEl });
    },
  });
  const wrapper = mount(Harness);
  const textarea = wrapper.element as HTMLTextAreaElement;
  const measure = (width: number, scrollHeight: number) => {
    Object.defineProperty(textarea, "clientWidth", { value: width, configurable: true });
    Object.defineProperty(textarea, "scrollHeight", { value: scrollHeight, configurable: true });
  };
  return { state, identity, wrapper, textarea, measure, scene: useDiscoverySceneState() };
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe("useProfileInputScene", () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  it("保存画像框高度现场，同轮同宽同内容时接回（不重算）", async () => {
    const { state, identity, textarea, measure, scene } =
      harness(`profile-input-save-${Date.now()}-${Math.random()}`);
    measure(320, 200);

    state.activeStep.value = "search";
    await flush();
    expect(textarea.style.height).toBe("200px");
    expect(scene.getCurrent(identity.value).profileInputHeight).toBe(200);
    expect(scene.getCurrent(identity.value).profileInputWidth).toBe(320);

    // 内容高度"变大"后再回到页面二：仍接回现场高度（同宽同内容），不被重算覆盖。
    measure(320, 500);
    state.activeStep.value = "results";
    await flush();
    state.activeStep.value = "search";
    await flush();
    expect(textarea.style.height).toBe("200px");
    expect(scene.getCurrent(identity.value).profileInputHeight).toBe(200);

    // 窗口变窄：宽度变了 → 重算，并把最新高度/宽度写回现场。
    measure(200, 500);
    window.dispatchEvent(new Event("resize"));
    await flush();
    expect(textarea.style.height).toBe("500px");
    expect(scene.getCurrent(identity.value).profileInputHeight).toBe(500);
    expect(scene.getCurrent(identity.value).profileInputWidth).toBe(200);
  });

  it("内容变化后重算并回写最新高度", async () => {
    const { state, identity, textarea, measure, scene } =
      harness(`profile-input-content-${Date.now()}-${Math.random()}`);
    measure(320, 200);
    state.activeStep.value = "search";
    await flush();
    expect(textarea.style.height).toBe("200px");

    measure(320, 700);
    state.profileSummary.value = "三年 Python 后端，期望 AI 应用开发方向";
    await flush();
    expect(textarea.style.height).toBe("700px");
    expect(scene.getCurrent(identity.value).profileInputContent)
      .toBe("三年 Python 后端，期望 AI 应用开发方向");
  });

  it("开新一轮换身份后不沿用旧轮高度（新身份按当前内容重算）", async () => {
    const profileId = `profile-input-rotate-${Date.now()}-${Math.random()}`;
    const { state, identity, textarea, measure, scene } = harness(profileId);
    measure(320, 200);
    state.activeStep.value = "search";
    await flush();
    const oldEpoch = identity.value.runEpoch;
    expect(scene.getCurrent(identity.value).profileInputHeight).toBe(200);

    scene.rotateRoundEpoch(profileId);
    await flush();
    expect(identity.value.runEpoch).not.toBe(oldEpoch);

    measure(320, 700);
    state.profileSummary.value = "新一轮的求职画像";
    await flush();
    expect(textarea.style.height).toBe("700px");
    expect(scene.getCurrent(identity.value).profileInputHeight).toBe(700);
    expect(scene.getCurrent({ profileId, runEpoch: oldEpoch, platform: "boss" }).profileInputHeight)
      .toBe(200);
  });
});
