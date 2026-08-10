<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { Activity, RefreshCw, ShieldAlert, Wrench } from "@lucide/vue";
import BaseDialog from "./BaseDialog.vue";
import { apiRequest, errorMessage } from "../api";
import type { Notice } from "../types";

// 与后端 collect_check_items / /api/env-check 的契约（webui/app.py env_check）。
interface CheckItem {
  id: string;
  name: string;
  status: "ok" | "fail" | "skip";
  detail: string;
  fix: string | null;
}

interface CheckGroup {
  id: string;
  name: string;
  items: CheckItem[];
}

interface CooldownRecord {
  account_id: string;
  platform: string;
  until: number;
  until_text: string;
  reason: string;
  from_run?: string;
}

interface EnvCheckResponse {
  ok: boolean;
  runtime_mode?: "source" | "exe";
  groups: CheckGroup[];
  active_account: string;
  cooldowns: CooldownRecord[];
  checked_at: number;
}

const props = defineProps<{ open: boolean }>();
const emit = defineEmits<{
  close: [];
  "open-browser-accounts": [];
  "open-ai-settings": [];
}>();

const loading = ref(false);
const groups = ref<CheckGroup[]>([]);
const cooldowns = ref<CooldownRecord[]>([]);
const activeAccount = ref("");
const checkedAt = ref<number | null>(null);
// 运行时模式：仅用于展示差异文案（EXE 模式 deps=内置运行时、新增 webview2 项），不改变检查流程。
// 响应缺失 runtime_mode 时默认 "source"，保证源码模式零回归（合同 §2.3、§4）。
const runtimeMode = ref<"source" | "exe">("source");
const localNotice = ref<Notice | null>(null);
const busyAction = ref(""); // 正在执行的修复动作 id，避免重复点击
const accountNames = ref<Record<string, string>>({});
const pendingCooldown = ref<CooldownRecord | null>(null);

const PLATFORM_LABELS: Record<string, string> = {
  boss: "BOSS",
  zhilian: "智联",
};

function setLocalNotice(notice: Notice) {
  localNotice.value = notice;
}

const checkedAtText = computed(() => {
  if (!checkedAt.value) return "";
  const date = new Date(checkedAt.value * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
});

watch(() => props.open, (open) => {
  if (open) {
    localNotice.value = null;
    void loadAccounts();
    void runCheck();
  } else {
    busyAction.value = "";
  }
});

async function loadAccounts() {
  try {
    const data = await apiRequest<{ accounts?: { id: string; name: string }[] }>(
      "/api/browser-accounts",
    );
    const next: Record<string, string> = {};
    for (const acc of data.accounts || []) next[acc.id] = acc.name;
    accountNames.value = next;
  } catch {
    // 账号名只是展示辅助，失败不影响检查主体。
  }
}

async function runCheck() {
  loading.value = true;
  try {
    const data = await apiRequest<EnvCheckResponse>("/api/env-check");
    groups.value = data.groups || [];
    cooldowns.value = data.cooldowns || [];
    activeAccount.value = data.active_account || "";
    checkedAt.value = data.checked_at || null;
    runtimeMode.value = data.runtime_mode === "exe" ? "exe" : "source";
  } catch (error) {
    setLocalNotice({ message: errorMessage(error, "环境检查失败"), tone: "error" });
  } finally {
    loading.value = false;
  }
}

async function testAiConnection() {
  busyAction.value = "ai_test";
  try {
    const data = await apiRequest<{ ok?: boolean; message?: string }>(
      "/api/ai-settings/test",
      { method: "POST" },
    );
    setLocalNotice({
      message: data.ok ? "AI 连通性测试通过" : `AI 连通性测试失败：${data.message || ""}`,
      tone: data.ok ? "success" : "error",
    });
  } catch (error) {
    setLocalNotice({ message: errorMessage(error, "AI 连通性测试失败"), tone: "error" });
  } finally {
    busyAction.value = "";
  }
}

// 修复动作：登录指引、打开 AI 设置。其余 fix 文案仅作提示展示，不生成按钮。
function fixAction(item: CheckItem): { label: string; run: () => Promise<void> } | null {
  const fix = item.fix || "";
  if (fix.includes("打开账号浏览器登录") || fix.includes("浏览器登录")) {
    return {
      label: "登录指引",
      run: async () => {
        emit("open-browser-accounts");
      },
    };
  }
  if (fix.includes("打开 AI 设置")) {
    return {
      label: "打开 AI 设置",
      run: async () => {
        emit("open-ai-settings");
      },
    };
  }
  return null;
}

async function runFix(item: CheckItem) {
  const action = fixAction(item);
  if (!action || busyAction.value) return;
  busyAction.value = item.id;
  try {
    await action.run();
  } catch (error) {
    setLocalNotice({ message: errorMessage(error, "修复操作失败"), tone: "error" });
  } finally {
    busyAction.value = "";
  }
}

// 手动解除冷却：先打开应用内确认弹窗，只清 cooldown 不碰登录态缓存（D6）。
function clearCooldown(record: CooldownRecord) {
  pendingCooldown.value = record;
}

async function confirmClearCooldown() {
  const record = pendingCooldown.value;
  if (!record || busyAction.value) return;
  pendingCooldown.value = null;
  busyAction.value = `clear:${record.account_id}:${record.platform}`;
  try {
    await apiRequest("/api/cooldown/clear", {
      method: "POST",
      json: { account_id: record.account_id, platform: record.platform },
    });
    cooldowns.value = cooldowns.value.filter(
      (item) => item.account_id !== record.account_id || item.platform !== record.platform,
    );
    setLocalNotice({ message: "冷却已解除", tone: "success" });
  } catch (error) {
    setLocalNotice({ message: errorMessage(error, "解除冷却失败"), tone: "error" });
  } finally {
    busyAction.value = "";
  }
}
</script>

<template>
  <BaseDialog
    id="env-check"
    :open="open"
    title="环境检查"
    description="检查浏览器、AI 配置与本地环境是否就绪；发现问题可一键修复。"
    size="md"
    @close="$emit('close')"
  >
    <div class="env-check-toolbar">
      <span v-if="checkedAt" class="env-check-time" data-testid="env-check-time">
        上次检查：{{ checkedAtText }}
      </span>
      <button
        type="button"
        class="button secondary small"
        data-testid="env-check-rerun"
        :disabled="loading"
        @click="runCheck"
      >
        <RefreshCw :size="15" aria-hidden="true" />
        {{ loading ? "检查中…" : "重新检查" }}
      </button>
    </div>

    <div class="env-check-groups" :data-runtime-mode="runtimeMode">
      <section
        v-for="group in groups"
        :key="group.id"
        class="env-check-group"
        :data-testid="`env-group-${group.id}`"
      >
        <h3 class="env-check-group-title">{{ group.name }}</h3>
        <ul class="env-check-items">
          <li
            v-for="item in group.items"
            :key="item.id"
            class="env-check-item"
            :data-testid="`env-item-${item.id}`"
          >
            <span
              class="env-check-mark"
              :data-state="item.status"
              aria-hidden="true"
            >
              <template v-if="item.status === 'ok'">✅</template>
              <template v-else-if="item.status === 'fail'">❌</template>
              <template v-else>⏭️</template>
            </span>
            <div class="env-check-item-body">
              <strong class="env-check-item-name">{{ item.name }}</strong>
              <p class="env-check-item-detail">{{ item.detail }}</p>
            </div>
            <button
              v-if="item.status === 'fail' && fixAction(item)"
              type="button"
              class="button secondary small env-check-fix"
              :disabled="Boolean(busyAction)"
              @click="runFix(item)"
            >
              <Wrench :size="13" aria-hidden="true" />
              {{ fixAction(item)!.label }}
            </button>
          </li>
        </ul>
        <button
          v-if="group.id === 'ai'"
          type="button"
          class="button secondary small env-check-ai-test"
          data-testid="env-ai-test"
          :disabled="Boolean(busyAction) || loading"
          @click="testAiConnection"
        >
          <Activity :size="14" aria-hidden="true" />
          {{ busyAction === 'ai_test' ? "测试中…" : "测试 AI 连通性" }}
        </button>
      </section>
    </div>

    <section
      v-if="cooldowns.length"
      class="env-check-cooldowns"
      data-testid="env-cooldowns"
    >
      <h3 class="env-check-group-title">
        <ShieldAlert :size="15" aria-hidden="true" />
        风控冷却提醒
      </h3>
      <p class="env-check-cooldown-tip">
        命中风控的账号在冷却期内不建议提交任务；连坐风险提示：其他账号抓取也可能受影响。
      </p>
      <ul class="env-check-cooldown-list">
        <li
          v-for="record in cooldowns"
          :key="`${record.account_id}:${record.platform}`"
          class="env-check-cooldown-item"
          :data-testid="`cooldown-${record.account_id}-${record.platform}`"
        >
          <div class="env-check-cooldown-main">
            <strong>
              {{ accountNames[record.account_id] || record.account_id }}
              <span class="env-check-cooldown-platform">
                {{ PLATFORM_LABELS[record.platform] || record.platform }}
              </span>
            </strong>
            <p>建议等待至 {{ record.until_text }} 后重试</p>
            <p v-if="record.reason" class="env-check-cooldown-reason">{{ record.reason }}</p>
            <p v-if="record.from_run" class="env-check-cooldown-source" data-testid="cooldown-from-run">来源任务：{{ record.from_run }}</p>
          </div>
          <button
            type="button"
            class="button danger small"
            :disabled="Boolean(busyAction)"
            @click="clearCooldown(record)"
          >
            解除冷却
          </button>
        </li>
      </ul>
    </section>


    <div
      v-if="localNotice"
      class="env-check-notice"
      :data-tone="localNotice.tone"
      role="status"
      aria-live="polite"
    >
      {{ localNotice.message }}
    </div>
  </BaseDialog>

  <BaseDialog
    id="cooldown-confirm"
    :open="Boolean(pendingCooldown)"
    title="解除风控冷却"
    size="sm"
    @close="pendingCooldown = null"
  >
    <p class="env-check-confirm-text">
      解除「{{ pendingCooldown ? (accountNames[pendingCooldown.account_id] || pendingCooldown.account_id) : '' }}」的{{ pendingCooldown ? (PLATFORM_LABELS[pendingCooldown.platform] || pendingCooldown.platform) : '' }}风控冷却？解除后仍建议等待风控缓解后再跑任务。
    </p>
    <template #footer>
      <button
        type="button"
        class="button secondary"
        data-testid="cooldown-confirm-cancel"
        @click="pendingCooldown = null"
      >取消</button>
      <button
        type="button"
        class="button danger"
        data-testid="cooldown-confirm-ok"
        :disabled="Boolean(busyAction)"
        @click="confirmClearCooldown"
      >确认解除</button>
    </template>
  </BaseDialog>
</template>

<style scoped>
.env-check-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 14px;
}
.env-check-time {
  color: var(--muted);
  font-size: 12px;
}
.env-check-groups {
  display: grid;
  gap: 16px;
  max-height: min(46vh, 420px);
  overflow-y: auto;
  padding-right: 4px;
}
.env-check-group-title {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0 0 8px;
  font-size: 14px;
  color: var(--ink-1);
}
.env-check-items {
  display: grid;
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.env-check-item {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 10px 12px;
  border: 1px solid var(--hair);
  border-radius: 9px;
  background: var(--panel);
}
.env-check-mark {
  flex: 0 0 auto;
  width: 20px;
  text-align: center;
  font-size: 14px;
  line-height: 1.5;
}
.env-check-mark[data-state="pending"] {
  filter: grayscale(0.6);
  opacity: 0.65;
}
.env-check-item-body {
  flex: 1 1 auto;
  min-width: 0;
}
.env-check-item-name {
  display: block;
  font-size: 13px;
  color: var(--ink-1);
}
.env-check-item-detail {
  margin: 3px 0 0;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.45;
}
.env-check-fix {
  flex: 0 0 auto;
  align-self: center;
  white-space: nowrap;
}
.env-check-ai-test {
  margin-top: 8px;
}
.env-check-cooldowns {
  margin-top: 16px;
  padding-top: 14px;
  border-top: 1px solid var(--hair);
}
.env-check-cooldown-tip {
  margin: 0 0 10px;
  color: var(--unsure-deep);
  font-size: 12px;
  line-height: 1.5;
}
.env-check-cooldown-list {
  display: grid;
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.env-check-cooldown-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 12px;
  border: 1px solid var(--unsure-edge, var(--hair));
  border-radius: 9px;
  background: var(--unsure-wash);
}
.env-check-cooldown-main {
  flex: 1 1 auto;
  min-width: 0;
}
.env-check-cooldown-main strong {
  font-size: 13px;
  color: var(--ink-1);
}
.env-check-cooldown-platform {
  margin-left: 6px;
  padding: 1px 7px;
  border-radius: 999px;
  background: var(--hair-2);
  color: var(--muted);
  font-size: 11px;
}
.env-check-cooldown-main p {
  margin: 3px 0 0;
  color: var(--unsure-deep);
  font-size: 12px;
}
.env-check-cooldown-reason {
  opacity: 0.85;
}
.env-check-cooldown-source {
  opacity: 0.75;
  font-size: 11px;
}
.env-check-notice {
  margin-top: 14px;
  padding: 9px 11px;
  border-radius: 7px;
  color: var(--brand-ink);
  background: var(--brand-wash);
  font-size: 13px;
}
.env-check-notice[data-tone="success"] {
  color: var(--match-deep);
  background: var(--match-wash);
}
.env-check-notice[data-tone="error"] {
  color: var(--reject-deep);
  background: var(--reject-wash);
}
.env-check-notice[data-tone="warning"] {
  color: var(--unsure-deep);
  background: var(--unsure-wash);
}
</style>
