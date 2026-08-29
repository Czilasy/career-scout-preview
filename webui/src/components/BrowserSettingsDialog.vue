<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { Check, CircleAlert, LoaderCircle } from "@lucide/vue";
import BaseDialog from "./BaseDialog.vue";
import { browserRegistryApi, errorMessage } from "../api";
import type {
  BrowserRegistryEntryState,
  BrowserRegistryResponse,
  BrowserSelection,
} from "../api";

const props = defineProps<{ open: boolean }>();
const emit = defineEmits<{ close: [] }>();

const entries = ref<BrowserRegistryEntryState[]>([]);
const selectedKey = computed(() =>
  selection.value.mode === "registry" ? (selection.value.key || "") : ""
);

// 零安装指引（FR-010/US3 场景 7）：清单内一个都没探测到且未选手动时提示
const noneInstalled = computed(() =>
  entries.value.length > 0 && entries.value.every((entry) => !entry.installed)
);
const selection = ref<BrowserSelection>({ mode: "auto" });
const effectivePath = ref<string | null>(null);
const loading = ref(false);
const saving = ref(false);
const noticeText = ref("");
const noticeTone = ref<"success" | "error">("success");

// 手动指定路径分支
const manualMode = ref(false);
const manualPath = ref("");
const validating = ref(false);
const validationText = ref("");
const validationOk = ref(false);

async function load() {
  loading.value = true;
  try {
    const data: BrowserRegistryResponse = await browserRegistryApi.get();
    entries.value = data.registry;
    selection.value = data.selection;
    effectivePath.value = data.effective_path;
    // 手动模式下把已存路径带入输入框
    manualMode.value = data.selection.mode === "manual";
    if (data.selection.mode === "manual") {
      manualPath.value = data.selection.manual_path || "";
    }
  } catch (error) {
    noticeText.value = errorMessage(error, "浏览器清单加载失败");
    noticeTone.value = "error";
  } finally {
    loading.value = false;
  }
}

watch(() => props.open, (open) => {
  if (open) {
    noticeText.value = "";
    validationText.value = "";
    void load();
  }
});

function selectAuto() {
  manualMode.value = false;
  selection.value = { mode: "auto" };
}

function selectRegistry(key: string, installed: boolean) {
  if (!installed) return; // 未安装置灰，不可选
  manualMode.value = false;
  selection.value = { mode: "registry", key };
}

function selectManual() {
  manualMode.value = true;
  selection.value = { mode: "manual", manual_path: manualPath.value };
}

async function validateManualPath() {
  if (!manualPath.value.trim() || validating.value) return;
  validating.value = true;
  validationText.value = "";
  try {
    const result = await browserRegistryApi.validatePath(manualPath.value.trim());
    validationOk.value = Boolean(result.ok);
    validationText.value = result.ok
      ? `校验通过：${result.version}`
      : (result as { message: string }).message;
  } catch (error) {
    validationOk.value = false;
    validationText.value = errorMessage(error, "路径校验失败");
  } finally {
    validating.value = false;
  }
}

async function save() {
  if (saving.value) return;
  saving.value = true;
  noticeText.value = "";
  try {
    const payload = manualMode.value
      ? { mode: "manual" as const, path: manualPath.value.trim() }
      : selection.value.mode === "registry"
        ? { mode: "registry" as const, key: selection.value.key as string }
        : { mode: "auto" as const };
    const data = await browserRegistryApi.save(payload);
    selection.value = data.selection;
    effectivePath.value = data.effective_path;
    noticeText.value = "已保存，抓取将使用所选浏览器（切换浏览器后需重新登录）";
    noticeTone.value = "success";
  } catch (error) {
    noticeText.value = errorMessage(error, "保存失败");
    noticeTone.value = "error";
  } finally {
    saving.value = false;
  }
}

function optionClass(key: string) {
  return selectedKey.value === key && !manualMode.value ? "browser-option selected" : "browser-option";
}



</script>

<template>
  <BaseDialog
    :open="props.open"
    title="浏览器"
    description="选择抓取使用的 Chromium 内核浏览器；数据目录按浏览器隔离，切换后各账号需重新登录一次。"
    initial-focus="[data-testid='browser-option-chrome']"
    @close="emit('close')"
  >
    <div data-testid="browser-settings-dialog" class="browser-settings">
      <p v-if="loading" class="browser-loading">探测中…</p>
      <template v-else>
        <button
          type="button"
          :class="selection.mode === 'auto' && !manualMode ? 'browser-option selected' : 'browser-option'"
          data-testid="browser-option-auto"
          @click="selectAuto"
        >
          <span class="browser-option-name">自动探测</span>
          <span class="browser-option-hint">按优先级使用已安装的浏览器（推荐）</span>
          <Check v-if="selection.mode === 'auto' && !manualMode" class="browser-check" :size="16" />
        </button>
        <button
          v-for="entry in entries"
          :key="entry.key"
          type="button"
          :class="optionClass(entry.key)"
          :disabled="!entry.installed"
          :data-testid="`browser-option-${entry.key}`"
          @click="selectRegistry(entry.key, entry.installed)"
        >
          <span class="browser-option-name">{{ entry.name }}</span>
          <span v-if="entry.installed" class="browser-option-hint">{{ entry.path }}</span>
          <span v-else class="browser-option-hint browser-missing">未安装</span>
          <Check v-if="selectedKey === entry.key && !manualMode" class="browser-check" :size="16" />
        </button>
        <p
          v-if="noneInstalled && !manualMode"
          class="browser-zero-hint"
          data-testid="browser-zero-hint"
        >
          未探测到已安装的浏览器，请在下方手动指定路径。
        </p>
        <button
          type="button"
          :class="manualMode ? 'browser-option selected' : 'browser-option'"
          data-testid="browser-option-manual"
          @click="selectManual"
        >
          <span class="browser-option-name">手动指定路径</span>
          <span class="browser-option-hint">清单之外的浏览器，填可执行文件路径</span>
          <Check v-if="manualMode" class="browser-check" :size="16" />
        </button>

        <div v-if="manualMode" class="browser-manual">
          <input
            v-model="manualPath"
            type="text"
            class="browser-manual-input"
            placeholder="浏览器可执行文件完整路径（如 chrome.exe / brave.exe）"
            data-testid="browser-manual-input"
            @input="validationText = ''"
          />
          <button
            type="button"
            class="browser-validate"
            data-testid="browser-validate-button"
            :disabled="validating || !manualPath.trim()"
            @click="validateManualPath"
          >
            <LoaderCircle v-if="validating" class="spin" :size="15" />
            {{ validating ? "校验中…" : "校验路径" }}
          </button>
          <p
            v-if="validationText"
            :class="validationOk ? 'browser-validation ok' : 'browser-validation bad'"
            data-testid="browser-validation-message"
          >
            {{ validationText }}
          </p>
        </div>

        <p v-if="effectivePath" class="browser-effective" data-testid="browser-effective-path">
          当前生效：{{ effectivePath }}
        </p>

        <p
          v-if="noticeText"
          :class="noticeTone === 'success' ? 'browser-notice ok' : 'browser-notice bad'"
          data-testid="browser-save-notice"
        >
          <CircleAlert v-if="noticeTone === 'error'" :size="15" />
          {{ noticeText }}
        </p>
      </template>
    </div>

    <template #footer>
      <button class="button" type="button" @click="emit('close')">关闭</button>
      <button
        class="button primary"
        type="button"
        data-testid="browser-save-button"
        :disabled="loading || saving || (manualMode && !manualPath.trim())"
        @click="save"
      >
        <LoaderCircle v-if="saving" class="spin" :size="15" />
        {{ saving ? "保存中…" : "保存" }}
      </button>
    </template>
  </BaseDialog>
</template>

<style scoped>
.browser-settings {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.browser-loading {
  margin: 0;
  color: var(--text-muted, #8b949e);
}

.browser-option {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  width: 100%;
  padding: 10px 12px;
  border: 1px solid var(--border, #30363d);
  border-radius: 8px;
  background: transparent;
  color: inherit;
  text-align: left;
  cursor: pointer;
  position: relative;
}

.browser-option:hover:not(:disabled) {
  border-color: var(--accent, #58a6ff);
}

.browser-option.selected {
  border-color: var(--accent, #58a6ff);
  background: color-mix(in srgb, var(--accent, #58a6ff) 10%, transparent);
}

.browser-option:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.browser-option-name {
  font-weight: 600;
}

.browser-option-hint {
  font-size: 12px;
  color: var(--text-muted, #8b949e);
  word-break: break-all;
}

.browser-zero-hint {
  margin: 0;
  font-size: 13px;
  color: var(--warning, #d29922);
}

.browser-missing {
  color: var(--danger, #f85149);
}

.browser-check {
  position: absolute;
  right: 12px;
  top: 12px;
  color: var(--accent, #58a6ff);
}

.browser-manual {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px 12px;
  border: 1px dashed var(--border, #30363d);
  border-radius: 8px;
}

.browser-manual-input {
  width: 100%;
  padding: 8px 10px;
  border: 1px solid var(--border, #30363d);
  border-radius: 6px;
  background: transparent;
  color: inherit;
}

.browser-validate {
  align-self: flex-start;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  border: 1px solid var(--border, #30363d);
  border-radius: 6px;
  background: transparent;
  color: inherit;
  cursor: pointer;
}

.browser-validation.ok {
  color: var(--success, #3fb950);
}

.browser-validation.bad {
  color: var(--danger, #f85149);
}

.browser-effective {
  margin: 0;
  font-size: 12px;
  color: var(--text-muted, #8b949e);
  word-break: break-all;
}

.browser-notice {
  margin: 0;
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
}

.browser-notice.ok {
  color: var(--success, #3fb950);
}

.browser-notice.bad {
  color: var(--danger, #f85149);
}

.spin {
  animation: browser-spin 1s linear infinite;
}

@keyframes browser-spin {
  to { transform: rotate(360deg); }
}
</style>
