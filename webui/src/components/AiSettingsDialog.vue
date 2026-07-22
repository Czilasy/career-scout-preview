<script setup lang="ts">
import { ref, watch } from "vue";
import BaseDialog from "./BaseDialog.vue";
import { apiRequest, errorMessage } from "../api";
import type { Notice } from "../types";

const props = defineProps<{ open: boolean }>();
const emit = defineEmits<{ close: []; notify: [notice: Notice] }>();

const endpointUrl = ref("");
const apiKey = ref("");
const maskedKey = ref("");
const model = ref("");
const models = ref<string[]>([]);
const busy = ref<"load" | "save" | "test" | "models" | "">("");

watch(() => props.open, (open) => {
  if (open) void loadSettings();
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
    emit("notify", { message: errorMessage(error, "AI 设置加载失败"), tone: "error" });
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
    emit("notify", { message: "AI 设置已保存", tone: "success" });
    await loadSettings();
  } catch (error) {
    emit("notify", { message: errorMessage(error, "AI 设置保存失败"), tone: "error" });
  } finally {
    busy.value = "";
  }
}

async function fetchModels() {
  busy.value = "models";
  try {
    const data = await apiRequest<{ ok?: boolean; models?: string[] }>("/api/ai-settings/models");
    models.value = data.models || [];
    emit("notify", {
      message: models.value.length ? `已拉取 ${models.value.length} 个模型` : "服务未返回模型列表",
      tone: models.value.length ? "success" : "warning",
    });
  } catch (error) {
    emit("notify", { message: errorMessage(error, "模型列表拉取失败"), tone: "error" });
  } finally {
    busy.value = "";
  }
}

async function testConnection() {
  busy.value = "test";
  try {
    const data = await apiRequest<{ ok?: boolean }>("/api/ai-settings/test", { method: "POST", json: {} });
    emit("notify", {
      message: data.ok ? "AI 服务连接成功" : "AI 服务连接失败，请检查设置",
      tone: data.ok ? "success" : "error",
    });
  } catch (error) {
    emit("notify", { message: errorMessage(error, "AI 服务连接失败"), tone: "error" });
  } finally {
    busy.value = "";
  }
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
      <label class="field-label">
        <span>模型</span>
        <input v-model.trim="model" list="ai-model-list" placeholder="输入或选择模型">
        <datalist id="ai-model-list">
          <option v-for="item in models" :key="item" :value="item" />
        </datalist>
      </label>
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
