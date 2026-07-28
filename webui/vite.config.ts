import { createHash } from "node:crypto";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";
import vue from "@vitejs/plugin-vue";

const projectRoot = dirname(fileURLToPath(new URL("../pyproject.toml", import.meta.url)));
const backendFiles = [
  ...readdirSync(join(projectRoot, "webui"))
    .filter((name) => name.endsWith(".py"))
    .map((name) => join(projectRoot, "webui", name)),
  join(projectRoot, "scripts", "boss_cdp_raw.py"),
].sort((left, right) => {
  const a = relative(projectRoot, left).replaceAll("\\", "/");
  const b = relative(projectRoot, right).replaceAll("\\", "/");
  return a < b ? -1 : a > b ? 1 : 0;
});
const backendDigest = createHash("sha256");
for (const path of backendFiles) {
  const relativeName = relative(projectRoot, path).replaceAll("\\", "/");
  backendDigest.update(relativeName, "utf8");
  backendDigest.update(Buffer.from([0]));
  backendDigest.update(readFileSync(path));
  backendDigest.update(Buffer.from([0]));
}
const expectedBackendBuildHash = backendDigest.digest("hex").slice(0, 12);

export default defineConfig({
  plugins: [vue()],
  base: "/static/",
  define: {
    __EXPECTED_BACKEND_BUILD_HASH__: JSON.stringify(expectedBackendBuildHash),
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
  server: {
    proxy: {
      "/api": "http://127.0.0.1:5000",
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    css: false,
    setupFiles: ["./src/test/setup.ts"],
  },
});
