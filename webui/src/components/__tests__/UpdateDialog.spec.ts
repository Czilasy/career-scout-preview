import { flushPromises, mount } from "@vue/test-utils";
import UpdateDialog from "../UpdateDialog.vue";
import { expectedBackendBuildHash, setBuildIdentity } from "../../api";

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const updateInfo = {
  ok: true,
  current: "2.8.4",
  latest: "2.8.5",
  has_update: true,
  release_url: "https://github.com/Czilasy/career-scout-preview/releases",
  release_notes: "修复：应用内更新提示优化",
  asset_name: "CareerScout-v2.8.5.exe",
  asset_url: "https://github.com/Czilasy/career-scout-preview/releases/download/v2.8.5/CareerScout-v2.8.5.exe",
  asset_size: 1024,
  sha256_url: "https://github.com/Czilasy/career-scout-preview/releases/download/v2.8.5/CareerScout-v2.8.5.exe.sha256",
  reason: "",
  checked_at: 1785940000,
  runtime_mode: "exe",
  installable: true,
};

async function mountOpen(fetchMock: (input: RequestInfo | URL) => Promise<Response>) {
  vi.stubGlobal("fetch", fetchMock);
  const wrapper = mount(UpdateDialog, { props: { open: false, info: updateInfo } });
  await flushPromises();
  await wrapper.setProps({ open: true });
  await flushPromises();
  return wrapper;
}

beforeEach(() => {
  setBuildIdentity(expectedBackendBuildHash);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("UpdateDialog failure messages", () => {
  it.each([
    ["download_failed", "下载失败，请检查网络或磁盘空间后重试"],
    ["sha256_unavailable", "无法获取校验文件"],
    ["sha256_mismatch", "更新包校验失败"],
    ["invalid_download_url", "下载地址不合法"],
    ["updater_launch_failed", "更新脚本启动失败"],
  ] as const)("maps %s to a readable Chinese message", async (code, expected) => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/update-status") {
        return response({
          ok: true,
          status: "failed",
          received: 0,
          total: 0,
          progress: 0,
          path: "",
          error: code,
        });
      }
      return response({});
    });

    const wrapper = await mountOpen(fetchMock);

    expect(wrapper.get('[data-testid="update-error"]').text()).toContain(expected);
    expect(wrapper.text()).not.toContain(code);
  });

  it("falls back to pure Chinese for unknown failure codes", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/update-status") {
        return response({
          ok: true,
          status: "failed",
          received: 0,
          total: 0,
          progress: 0,
          path: "",
          error: "mystery_code",
        });
      }
      return response({});
    });

    const wrapper = await mountOpen(fetchMock);

    expect(wrapper.get('[data-testid="update-error"]').text())
      .toContain("更新失败，请重试；若仍失败请到 Release 页手动下载。");
    expect(wrapper.text()).not.toContain("mystery_code");
  });

  it("shows backend user_message when the download API fails", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/update-status") {
        return response({
          ok: true,
          status: "idle",
          received: 0,
          total: 0,
          progress: 0,
          path: "",
          error: "",
        });
      }
      if (url === "/api/update-download") {
        return response({
          ok: false,
          error_code: "download_failed",
          user_message: "下载失败，请检查网络或磁盘空间后重试；仍失败请到 Release 页手动下载。",
        }, 500);
      }
      return response({});
    });

    const wrapper = await mountOpen(fetchMock);
    await wrapper.get('[data-testid="update-download"]').trigger("click");
    await flushPromises();

    expect(wrapper.get('[data-testid="update-error"]').text())
      .toContain("下载失败，请检查网络或磁盘空间后重试");
    expect(wrapper.text()).not.toContain("download_failed");
  });

  it("shows backend user_message when the restart API fails", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/update-status") {
        return response({
          ok: true,
          status: "ready",
          received: 100,
          total: 100,
          progress: 1,
          path: "C:/Downloads/CareerScout-v2.8.5.exe",
          error: "",
        });
      }
      if (url === "/api/update-restart") {
        return response({
          ok: false,
          error_code: "updater_launch_failed",
          user_message: "更新脚本启动失败，请关闭软件后手动下载更新",
        }, 500);
      }
      return response({});
    });

    const wrapper = await mountOpen(fetchMock);
    await wrapper.get('[data-testid="update-restart"]').trigger("click");
    await flushPromises();

    expect(wrapper.get('[data-testid="update-error"]').text())
      .toContain("更新脚本启动失败");
    expect(wrapper.text()).not.toContain("updater_launch_failed");
  });
});
