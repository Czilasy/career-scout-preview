<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { Bot, BriefcaseBusiness, Star, Wifi, WifiOff } from "@lucide/vue";
import AiSettingsDialog from "./components/AiSettingsDialog.vue";
import NoticeBar from "./components/NoticeBar.vue";
import DiscoveryView from "./views/DiscoveryView.vue";
import { apiRequest, errorMessage, initializeSession } from "./api";
import type { CandidateProfile, Notice } from "./types";

const aiSettingsOpen = ref(false);
const connected = ref(false);
const profiles = ref<CandidateProfile[]>([]);
const currentProfileId = ref("");
const notice = ref<Notice | null>(null);
let noticeTimer: number | undefined;

const favoritesOpen = ref(false);
const favorites = ref<Record<string, unknown>[]>([]);

async function loadFavorites() {
  if (!currentProfileId.value) return;
  try {
    const data = await apiRequest<{ items?: Record<string, unknown>[] }>(
      `/api/favorites?profile_id=${encodeURIComponent(currentProfileId.value)}`,
    );
    favorites.value = data.items || [];
  } catch {
    favorites.value = [];
  }
}

const currentProfile = computed(() => (
  profiles.value.find((profile) => profile.id === currentProfileId.value) || null
));

onMounted(async () => {
  try {
    await initializeSession();
    const [check, profileData] = await Promise.all([
      apiRequest<{ connected?: boolean }>("/api/check"),
      apiRequest<{ profiles?: CandidateProfile[] }>("/api/profiles"),
    ]);
    connected.value = Boolean(check.connected);
    profiles.value = profileData.profiles || [];
    const saved = localStorage.getItem("boss-current-profile") || "";
    currentProfileId.value = profiles.value.some((profile) => profile.id === saved)
      ? saved
      : profiles.value[0]?.id || "";
  } catch (error) {
    showNotice({ message: errorMessage(error, "WebUI 初始化失败"), tone: "error" });
  }
});

onBeforeUnmount(() => {
  if (noticeTimer) window.clearTimeout(noticeTimer);
});

function selectProfile(profileId: string) {
  currentProfileId.value = profileId;
  if (profileId) localStorage.setItem("boss-current-profile", profileId);
}

function showNotice(next: Notice) {
  if (noticeTimer) window.clearTimeout(noticeTimer);
  notice.value = next;
  const delay = next.tone === "error" ? 8000 : next.tone === "warning" ? 5000 : 3000;
  noticeTimer = window.setTimeout(() => {
    notice.value = null;
    noticeTimer = undefined;
  }, delay);
}

function dismissNotice() {
  if (noticeTimer) window.clearTimeout(noticeTimer);
  noticeTimer = undefined;
  notice.value = null;
}

function acceptCreatedProfile(profile: CandidateProfile) {
  const index = profiles.value.findIndex((item) => item.id === profile.id);
  if (index >= 0) profiles.value[index] = profile;
  else profiles.value.push(profile);
  selectProfile(profile.id);
}
</script>

<template>
  <div class="app-shell">
    <header class="app-header">
      <a class="brand" href="/" aria-label="AI 求职工作台首页">
        <span class="brand-mark" aria-hidden="true"><BriefcaseBusiness :size="20" /></span>
        <span><strong>求职雷达</strong><small>BOSS 工作台</small></span>
      </a>


      <div class="header-actions">
        <span class="connection-chip" :data-connected="connected">
          <Wifi v-if="connected" :size="16" aria-hidden="true" />
          <WifiOff v-else :size="16" aria-hidden="true" />
          <span>{{ connected ? "浏览器已连接" : "浏览器未连接" }}</span>
        </span>
        <label class="profile-picker">
          <span class="sr-only">当前画像</span>
          <select :value="currentProfileId" aria-label="当前画像" @change="selectProfile(($event.target as HTMLSelectElement).value)">
            <option value="">{{ profiles.length ? "选择求职画像" : "暂无画像" }}</option>
            <option v-for="profile in profiles" :key="profile.id" :value="profile.id">{{ profile.name }}</option>
          </select>
        </label>
        <button
          class="button secondary favorites-trigger"
          type="button"
          aria-label="查看收藏"
          @click="favoritesOpen = !favoritesOpen; loadFavorites()"
        >
          <Star :size="18" aria-hidden="true" /><span>收藏</span>
          <em v-if="favorites.length" class="fav-badge">{{ favorites.length }}</em>
        </button>
        <button
          class="button secondary ai-settings-trigger"
          type="button"
          data-testid="ai-settings-trigger"
          aria-label="打开 AI 设置"
          @click="aiSettingsOpen = true"
        >
          <Bot :size="18" aria-hidden="true" /><span>AI 设置</span>
        </button>
      </div>
    </header>

    <aside v-if="favoritesOpen" class="fav-drawer">
      <div class="fav-drawer-head">
        <strong>我的收藏</strong>
        <button type="button" class="fav-drawer-close" aria-label="关闭收藏面板" @click="favoritesOpen = false">&times;</button>
      </div>
      <p v-if="!favorites.length" class="fav-drawer-empty">暂无收藏，在结果页点「收藏」即可添加。</p>
      <div v-else class="fav-drawer-list">
        <a
          v-for="job in favorites"
          :key="String(job.job_id || job.id)"
          class="fav-card"
          :href="String(job.job_link || '#')"
          target="_blank"
          rel="noopener"
        >
          <strong class="fav-card-title">{{ job.title || "未知岗位" }}</strong>
          <span class="fav-card-meta">{{ job.salary || "薪资面议" }} · {{ job.location || "" }}</span>
          <span class="fav-card-company">{{ job.company || "" }}</span>
        </a>
      </div>
    </aside>

    <NoticeBar :notice="notice" @dismiss="dismissNotice" />

    <div class="app-content">
      <DiscoveryView
        :profile-id="currentProfileId"
        @notify="showNotice"
        @profile-created="acceptCreatedProfile"
      />
    </div>

    <AiSettingsDialog
      :open="aiSettingsOpen"
      @close="aiSettingsOpen = false"
      @notify="showNotice"
    />
  </div>
</template>
