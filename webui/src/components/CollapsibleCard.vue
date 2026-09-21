<script setup lang="ts">
import { ChevronDown } from "@lucide/vue";
import { nextTick, onBeforeUnmount, ref, watch } from "vue";
import type { SceneIdentity } from "../types";
import { useDiscoverySceneState } from "../composables/useDiscoverySceneState";

const props = defineProps<{
  title: string;
  modelValue: boolean;
  /** static 模式：常驻展开，卡头不可点击、不显示折叠箭头（如步骤 2 双栏卡）。 */
  static?: boolean;
  /** 将操作插入标题内容中，避免操作按钮贴在卡片最外侧。 */
  actionsInHeader?: boolean;
  /** Spec041：可选的步骤页现场身份与卡片键。 */
  sceneIdentity?: SceneIdentity;
  sceneCardKey?: string;
}>();

const emit = defineEmits<{
  "update:modelValue": [value: boolean];
}>();

function toggle() {
  if (props.static) return;
  emit("update:modelValue", !props.modelValue);
}

const innerEl = ref<HTMLElement | null>(null);
const sceneStore = useDiscoverySceneState();
let sceneReady = false;

function restoreCardScene(): void {
  sceneReady = false;
  const identity = props.sceneIdentity;
  if (!identity || !props.sceneCardKey) {
    sceneReady = true;
    return;
  }
  const scene = sceneStore.getCurrent(identity);
  const top = scene.cardScrollTops[props.sceneCardKey] || 0;
  if (!props.static && Object.prototype.hasOwnProperty.call(scene.cardOpenStates, props.sceneCardKey)) {
    emit("update:modelValue", Boolean(scene.cardOpenStates[props.sceneCardKey]));
  }
  sceneReady = true;
  void nextTick(() => {
    if (innerEl.value) innerEl.value.scrollTop = top;
  });
}

function persistCardOpen(value: boolean): void {
  if (!sceneReady || !props.sceneIdentity || !props.sceneCardKey || props.static) return;
  const current = sceneStore.getCurrent(props.sceneIdentity);
  sceneStore.saveCurrent(props.sceneIdentity, {
    cardOpenStates: {
      ...current.cardOpenStates,
      [props.sceneCardKey]: value,
    },
  });
}

function persistCardScroll(): void {
  if (!sceneReady || !props.sceneIdentity || !props.sceneCardKey) return;
  const current = sceneStore.getCurrent(props.sceneIdentity);
  sceneStore.saveCurrent(props.sceneIdentity, {
    cardScrollTops: {
      ...current.cardScrollTops,
      [props.sceneCardKey]: innerEl.value?.scrollTop || 0,
    },
  });
}

watch(
  () => [props.sceneIdentity?.profileId, props.sceneIdentity?.runEpoch, props.sceneIdentity?.platform, props.sceneCardKey],
  restoreCardScene,
  { immediate: true },
);

watch(() => props.modelValue, persistCardOpen);

onBeforeUnmount(persistCardScroll);
</script>

<template>
  <div class="collapsible-card content-card" :class="{ open: modelValue || static }">
    <div class="collapsible-header-row" :class="{ 'has-header-actions': actionsInHeader }">
      <template v-if="actionsInHeader">
        <component
          :is="static ? 'div' : 'button'"
          :type="static ? undefined : 'button'"
          class="collapsible-header"
          :class="{ 'is-static': static }"
          :aria-expanded="static ? undefined : modelValue"
          @click="toggle"
        >
          <span class="collapsible-prefix"><slot name="prefix" /></span>
          <span class="collapsible-title">{{ title }}</span>
          <span class="collapsible-header-extra">
            <slot name="summary" />
            <ChevronDown v-if="!static" :size="16" class="collapsible-chevron" aria-hidden="true" />
          </span>
        </component>
        <div class="collapsible-header-actions" @click.stop>
          <slot name="actions" />
        </div>
      </template>
      <component
        v-else
        :is="static ? 'div' : 'button'"
        :type="static ? undefined : 'button'"
        class="collapsible-header"
        :class="{ 'is-static': static }"
        :aria-expanded="static ? undefined : modelValue"
        @click="toggle"
      >
        <span class="collapsible-prefix"><slot name="prefix" /></span>
        <span class="collapsible-title">{{ title }}</span>
        <span class="collapsible-header-extra">
          <slot name="summary" />
          <ChevronDown v-if="!static" :size="16" class="collapsible-chevron" aria-hidden="true" />
        </span>
      </component>
      <slot v-if="!actionsInHeader" name="actions" />
    </div>
    <div class="collapsible-body" :class="{ open: modelValue || static }">
      <div class="collapsible-inner" ref="innerEl" @scroll="persistCardScroll">
        <div class="collapsible-content">
          <slot />
        </div>
      </div>
    </div>
  </div>
</template>
