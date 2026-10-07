import { mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it } from "vitest";
import CollapsibleCard from "../CollapsibleCard.vue";
import { useDiscoverySceneState } from "../../composables/useDiscoverySceneState";
import type { SceneIdentity } from "../../types";

const identity: SceneIdentity = {
  profileId: "profile-card",
  runEpoch: "run-card",
  platform: "boss",
};

describe("CollapsibleCard scene state", () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  it("restores the open state saved for the same scene identity", async () => {
    const scene = useDiscoverySceneState();
    scene.saveCurrent(identity, { cardOpenStates: { search: true } } as never);

    const wrapper = mount(CollapsibleCard, {
      props: {
        title: "搜索范围",
        modelValue: false,
        sceneIdentity: identity,
        sceneCardKey: "search",
      },
    });

    await wrapper.vm.$nextTick();
    expect(wrapper.emitted("update:modelValue")).toEqual([[true]]);
  });

  it("自动收拢写既有 cardOpenStates，重新挂载（刷新）仍按该现场恢复", async () => {
    const scene = useDiscoverySceneState();
    const wrapper = mount(CollapsibleCard, {
      props: {
        title: "搜索范围",
        modelValue: true,
        sceneIdentity: identity,
        sceneCardKey: "search",
      },
    });
    await wrapper.vm.$nextTick();

    // 收拢与手动开合同一条既有链路：父层写回 v-model → persistCardOpen。
    await wrapper.setProps({ modelValue: false });
    await wrapper.vm.$nextTick();

    expect(scene.getCurrent(identity).cardOpenStates).toEqual({ search: false });

    // 刷新＝按同一 scene 重新挂载：恢复的现场仍是「收起」，没有新增偏好字段。
    const remounted = mount(CollapsibleCard, {
      props: {
        title: "搜索范围",
        modelValue: true,
        sceneIdentity: identity,
        sceneCardKey: "search",
      },
    });
    await remounted.vm.$nextTick();
    expect(remounted.emitted("update:modelValue")).toEqual([[false]]);
  });

  it("手动展开覆盖自动收拢存档，后续恢复以用户选择为准", async () => {
    const scene = useDiscoverySceneState();
    scene.saveCurrent(identity, { cardOpenStates: { search: false } } as never);
    const wrapper = mount(CollapsibleCard, {
      props: {
        title: "搜索范围",
        modelValue: false,
        sceneIdentity: identity,
        sceneCardKey: "search",
      },
    });
    await wrapper.vm.$nextTick();
    // 现场说收起、父层同值：这时不会产生用户可见的状态变化。
    expect(wrapper.emitted("update:modelValue")).toEqual([[false]]);

    // 用户手动展开：父层写回 true，现场随既有链路更新为展开。
    await wrapper.setProps({ modelValue: true });
    await wrapper.vm.$nextTick();
    expect(scene.getCurrent(identity).cardOpenStates).toEqual({ search: true });
  });
});
