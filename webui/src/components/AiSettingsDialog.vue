<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { ChevronDown } from "@lucide/vue";
import BaseDialog from "./BaseDialog.vue";
import { apiRequest, userFacingMessage } from "../api";
import type { Notice } from "../types";

const props = defineProps<{ open: boolean }>();
const emit = defineEmits<{ close: [] }>();

const endpointUrl = ref("");
const apiKey = ref("");
const maskedKey = ref("");
const model = ref("");
const models = ref<string[]>([]);
const busy = ref<"load" | "save" | "test" | "models" | "">("");
// 就地提示：所有操作的反馈都显示在按钮上方一行，不冒泡到主页 NoticeBar，
// 也不自动消失（下次操作覆盖或关对话框时清空）。
const localNotice = ref<Notice | null>(null);

// 模型字段自定义下拉：原生 <input list="datalist"> 在 Chrome 上单击 input
// 不会展开下拉，必须按方向键或点浏览器画的右侧小箭头。换成自定义下拉面板，
// 点 input 主体或右侧 chevron 都能展开。
const modelDropdownOpen = ref(false);
const modelFieldRef = ref<HTMLElement | null>(null);

const filteredModels = computed(() => {
  const kw = model.value.trim().toLowerCase();
  if (!kw) return models.value;
  return models.value.filter((m) => m.toLowerCase().includes(kw));
});

function setLocalNotice(notice: Notice) {
  localNotice.value = notice;
}

function clearLocalNotice() {
  localNotice.value = null;
}

function handleOutsideClick(event: MouseEvent) {
  if (!modelDropdownOpen.value) return;
  if (modelFieldRef.value && !modelFieldRef.value.contains(event.target as Node)) {
    modelDropdownOpen.value = false;
  }
}

onMounted(() => document.addEventListener("mousedown", handleOutsideClick));
onBeforeUnmount(() => document.removeEventListener("mousedown", handleOutsideClick));

watch(() => props.open, (open) => {
  if (open) {
    void loadSettings();
    clearLocalNotice();
  } else {
    modelDropdownOpen.value = false;
    clearLocalNotice();
  }
});

async function loadSettings() {
  busy.value = "load";
  try {
    const data = await apiRequest<Record<string, unknown>>("/api/ai-settings");
    endpointUrl.value = String(data.endpoint_url || "");
    model.value = String(data.model || "");
    maskedKey.value = String(data.masked_key || "");
    apiKey.value = "";
  } catch (error) {
    setLocalNotice({ message: userFacingMessage(error, "AI 设置加载失败"), tone: "error" });
  } finally {
    busy.value = "";
  }
}

async function saveSettings() {
  busy.value = "save";
  try {
    await apiRequest("/api/ai-settings", {
      method: "PUT",
      json: { endpoint_url: endpointUrl.value, api_key: apiKey.value, model: model.value },
    });
    setLocalNotice({ message: "AI 设置已保存", tone: "success" });
    await loadSettings();
  } catch (error) {
    setLocalNotice({ message: userFacingMessage(error, "AI 设置保存失败"), tone: "error" });
  } finally {
    busy.value = "";
  }
}

async function fetchModels() {
  busy.value = "models";
  clearLocalNotice();
  try {
    // 用当前对话框里填的值测，不必先保存。apiKey 为空时后端用已保存 key 兜底。
    const data = await apiRequest<{ ok?: boolean; models?: string[]; error_code?: string; user_message?: string }>(
      "/api/ai-settings/models",
      {
        method: "POST",
        json: {
          endpoint_url: endpointUrl.value,
          api_key: apiKey.value,
          model: model.value,
        },
      },
    );
    models.value = data.models || [];
    if (models.value.length) {
      setLocalNotice({ message: `已拉取 ${models.value.length} 个模型`, tone: "success" });
    } else if (data.ok === false) {
      setLocalNotice({ message: data.user_message || "模型列表拉取失败，请检查 AI 设置后重试", tone: "error" });
    } else {
      setLocalNotice({ message: "服务未返回模型列表", tone: "warning" });
    }
  } catch (error) {
    setLocalNotice({ message: userFacingMessage(error, "模型列表拉取失败，请检查 AI 设置后重试"), tone: "error" });
  } finally {
    busy.value = "";
  }
}

async function testConnection() {
  busy.value = "test";
  clearLocalNotice();
  try {
    // 用当前对话框里填的值测，不必先保存。
    const data = await apiRequest<{ ok?: boolean; warning_codes?: string[]; user_message?: string }>(
      "/api/ai-settings/test",
      {
        method: "POST",
        json: {
          endpoint_url: endpointUrl.value,
          api_key: apiKey.value,
          model: model.value,
        },
      },
    );
    if (data.ok) {
      setLocalNotice({ message: "AI 服务连接成功", tone: "success" });
    } else {
      setLocalNotice({ message: data.user_message || "AI 服务连接失败，请检查 AI 设置后重试", tone: "error" });
    }
  } catch (error) {
    setLocalNotice({ message: userFacingMessage(error, "AI 服务连接失败，请检查 AI 设置后重试"), tone: "error" });
  } finally {
    busy.value = "";
  }
}

function selectModel(item: string) {
  model.value = item;
  modelDropdownOpen.value = false;
}
</script>

<template>
  <BaseDialog
    id="ai-settings"
    :open="open"
    title="AI 设置"
    description="API 密钥仅由本地后端保存；前端不会回显完整密钥。"
    size="md"
    @close="$emit('close')"
  >
    <form class="form-stack" @submit.prevent="saveSettings">
      <label class="field-label">
        <span>API 地址</span>
        <input v-model.trim="endpointUrl" type="url" required placeholder="https://api.example.com/v1/chat/completions">
      </label>
      <label class="field-label">
        <span>API Key</span>
        <input v-model="apiKey" type="password" autocomplete="off" :placeholder="maskedKey || '输入 API Key'">
        <small v-if="maskedKey">已保存：{{ maskedKey }}；留空将继续使用原密钥。</small>
      </label>
      <div ref="modelFieldRef" class="field-label model-field">
        <span>模型</span>
        <div class="model-input-wrap">
          <input
            v-model.trim="model"
            type="text"
            placeholder="输入或选择模型"
            autocomplete="off"
            @click="modelDropdownOpen = true"
          >
          <button
            type="button"
            class="model-chevron"
            :aria-expanded="modelDropdownOpen"
            aria-label="展开模型列表"
            @click="modelDropdownOpen = !modelDropdownOpen"
          >
            <ChevronDown :size="16" />
          </button>
          <ul v-if="modelDropdownOpen && filteredModels.length" class="model-dropdown">
            <li
              v-for="item in filteredModels"
              :key="item"
              :class="{ active: item === model }"
              @mousedown.prevent="selectModel(item)"
            >
              {{ item }}
            </li>
          </ul>
        </div>
      </div>
      <div
        v-if="localNotice"
        class="ai-local-notice"
        :data-tone="localNotice.tone"
        role="status"
        aria-live="polite"
      >
        {{ localNotice.message }}
      </div>
      <div class="button-row">
        <button class="button primary" type="submit" :disabled="Boolean(busy)">
          {{ busy === "save" ? "保存中…" : "保存设置" }}
        </button>
        <button class="button secondary" type="button" :disabled="Boolean(busy)" aria-label="拉取可用模型" title="拉取可用模型" @click="fetchModels">
          {{ busy === "models" ? "拉取中…" : "拉取模型" }}
        </button>
        <button class="button ghost" type="button" :disabled="Boolean(busy)" @click="testConnection">
          {{ busy === "test" ? "测试中…" : "测试连接" }}
        </button>
      </div>
    </form>
  </BaseDialog>
</template>

<style scoped>
.model-field {
  position: relative;
}

.model-input-wrap {
  position: relative;
  display: flex;
  align-items: center;
}

.model-input-wrap input {
  width: 100%;
  padding-right: 2rem;
}

.model-chevron {
  position: absolute;
  right: 0.4rem;
  background: transparent;
  border: none;
  cursor: pointer;
  padding: 0.2rem;
  display: inline-flex;
  align-items: center;
  color: inherit;
  opacity: 0.6;
}

.model-chevron:hover {
  opacity: 1;
}

.model-dropdown {
  position: absolute;
  top: 100%;
  left: 0;
  right: 0;
  margin-top: 0.25rem;
  max-height: 14rem;
  overflow-y: auto;
  background: var(--panel);
  border: 1px solid var(--hair);
  border-radius: 0.5rem;
  box-shadow: var(--shadow);
  list-style: none;
  padding: 0.25rem 0;
  z-index: 10;
}

.model-dropdown li {
  padding: 0.4rem 0.75rem;
  cursor: pointer;
  font-size: 0.875rem;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.model-dropdown li:hover {
  background: var(--hair-2);
}

.model-dropdown li.active {
  background: var(--brand-wash);
  color: var(--brand-ink);
  font-weight: 600;
}

.ai-local-notice {
  font-size: 0.85rem;
  line-height: 1.3;
  padding: 0.45rem 0.65rem;
  border-radius: 0.35rem;
  border: 1px solid transparent;
  word-break: break-all;
}

.ai-local-notice[data-tone="success"] {
  color: var(--match-deep);
  background: var(--match-wash);
  border-color: var(--match-edge);
}

.ai-local-notice[data-tone="error"] {
  color: var(--reject-deep);
  background: var(--reject-wash);
  border-color: var(--reject-edge);
}

.ai-local-notice[data-tone="warning"] {
  color: var(--unsure-deep);
  background: var(--unsure-wash);
  border-color: var(--unsure-edge);
}

.ai-local-notice[data-tone="info"] {
  color: var(--brand-ink);
  background: var(--brand-wash);
  border-color: var(--brand-edge);
}
</style>
