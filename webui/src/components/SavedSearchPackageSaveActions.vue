<script setup lang="ts">
// Spec 044 B100 US1：第二页的保存动作。
//
// 组件只做交互与事件：真正落库由页面调用 useSearchPackages 完成，组件自身
// 不拼请求、不保存状态，也不因为任何编辑动作自动触发保存。
import { ref } from "vue";
import { Bookmark, LoaderCircle } from "@lucide/vue";
import BaseDialog from "./BaseDialog.vue";

const props = defineProps<{
  /** 由当前第二页内容算出的默认名称。 */
  defaultName: string;
  busy: boolean;
}>();

const emit = defineEmits<{
  save: [name: string];
}>();

const naming = ref(false);
const nameDraft = ref("");

function openNaming() {
  naming.value = true;
  nameDraft.value = props.defaultName || "常用搜索配置";
}

function cancelNaming() {
  naming.value = false;
  nameDraft.value = "";
}

function confirmNaming() {
  const mode = naming.value;
  const name = nameDraft.value.trim();
  naming.value = false;
  nameDraft.value = "";
  if (!mode) return;
  emit("save", name);
}

function onSaveClick() {
  if (props.busy) return;
  openNaming();
}
</script>

<template>
  <div class="saved-package-actions">
    <button
      class="button secondary small saved-package-save-button"
      type="button"
      data-testid="package-save"
      :disabled="busy"
      @click="onSaveClick"
    >
      <LoaderCircle v-if="busy" class="spin" :size="15" aria-hidden="true" />
      <Bookmark v-else :size="15" aria-hidden="true" />
      {{ busy ? "保存中…" : "保存为常用配置" }}
    </button>

    <BaseDialog
      :open="naming"
      title="保存为常用配置"
      size="xs"
      initial-focus="[data-testid='package-name-input']"
      :teleport="true"
      data-testid="package-save-modal"
      @close="cancelNaming"
    >
      <label class="field-label saved-package-name-field">
        <span>配置名称</span>
        <input
          v-model="nameDraft"
          data-testid="package-name-input"
          type="text"
          maxlength="80"
          placeholder="常用搜索配置"
          @keydown.enter.prevent="confirmNaming"
        >
      </label>
      <template #footer>
        <button
          class="button primary small"
          type="button"
          data-testid="package-confirm-save"
          :disabled="busy"
          @click="confirmNaming"
        >
          保存
        </button>
        <button
          class="button ghost small"
          type="button"
          data-testid="package-cancel-save"
          :disabled="busy"
          @click="cancelNaming"
        >
          取消
        </button>
      </template>
    </BaseDialog>
  </div>
</template>

<style scoped>
.saved-package-actions {
  display: flex;
  align-items: center;
  flex: 0 0 auto;
}

.saved-package-save-button {
  min-height: 36px;
  padding: 0 11px;
  font-size: 12px;
}

.saved-package-name-field {
  gap: 7px;
}
</style>
