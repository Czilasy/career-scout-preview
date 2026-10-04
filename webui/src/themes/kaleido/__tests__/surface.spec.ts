import { mount } from "@vue/test-utils";
import { nextTick } from "vue";
import { afterEach, expect, it, vi } from "vitest";
import ThemeSurfaceHost from "../../ThemeSurfaceHost.vue";
import { useTheme } from "../../../composables/useTheme";
import { mountKaleidoscope } from "../renderKaleidoscope";

vi.mock("../renderKaleidoscope", () => ({ mountKaleidoscope: vi.fn() }));
afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks(); });

it("mounts the special surface and disposes it when returning to a base theme", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({ mode: "light" }) }));
  const dispose = vi.fn();
  vi.mocked(mountKaleidoscope).mockReturnValue(dispose);
  const { toggleTheme } = useTheme();
  toggleTheme("light");
  const wrapper = mount(ThemeSurfaceHost);
  expect(wrapper.find("canvas").exists()).toBe(false);
  toggleTheme("kaleido");
  await nextTick();
  expect(wrapper.find('[aria-hidden="true"] canvas').exists()).toBe(true);
  expect(mountKaleidoscope).toHaveBeenCalledTimes(1);
  toggleTheme("dark");
  await nextTick();
  expect(wrapper.find("canvas").exists()).toBe(false);
  expect(dispose).toHaveBeenCalledTimes(1);
  toggleTheme("kaleido");
  await nextTick();
  expect(mountKaleidoscope).toHaveBeenCalledTimes(2);
  wrapper.unmount();
  expect(dispose).toHaveBeenCalledTimes(2);
  toggleTheme("light");
});
