import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

const projectRoot = dirname(fileURLToPath(new URL("../pyproject.toml", import.meta.url)));
const buildState = JSON.parse(
  readFileSync(join(projectRoot, "webui", "dist", "build-state.json"), "utf8"),
);

export default defineConfig({
  plugins: [vue()],
  base: "/static/",
  define: {
    __EXPECTED_BACKEND_BUILD_HASH__: JSON.stringify(buildState.backend),
  },
  server: {
    host: "127.0.0.1",
    port: 5178,
    strictPort: true,
    proxy: {
      "/api": "http://127.0.0.1:5055",
    },
  },
});
