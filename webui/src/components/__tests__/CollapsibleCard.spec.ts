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
});
