import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
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
  join(projectRoot, "scripts", "zhilian_cdp_raw.py"),
].sort((left: string, right: string) => {
  const a = relative(projectRoot, left).replaceAll("\\", "/");
  const b = relative(projectRoot, right).replaceAll("\\", "/");
  if (a < b) return -1;
  if (a > b) return 1;
  return 0;
});

function collectFiles(dir: string): string[] {
  const files: string[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) {
      files.push(...collectFiles(full));
    } else {
      files.push(full);
    }
  }
  return files;
}

// 批四 T072：测试文件不参与产物构建，也不该让“只改测试”触发 pre-push 重建。
// 口径必须与 webui/ensure_frontend_sync.py 的 _frontend_files() 完全一致
// （两边各算一份同一算法的指纹并比对），改这里必须同步改那边。
function isTestArtifact(path: string): boolean {
  const relativePath = relative(projectRoot, path).replaceAll("\\", "/");
  return (
    relativePath.startsWith("webui/src/test/")
    || relativePath.includes("/__tests__/")
    || relativePath.endsWith(".spec.ts")
  );
}

const frontendCandidates = [
  ...collectFiles(join(projectRoot, "webui", "src")),
  join(projectRoot, "webui", "index.html"),
  join(projectRoot, "webui", "vite.config.ts"),
].filter((path: string) => existsSync(path) && !isTestArtifact(path));

function sourceDigest(files: string[], root: string): string {
  const digest = createHash("sha256");
  const ordered: { path: string; name: string }[] = files
    .map((path) => ({ path, name: relative(root, path).replaceAll("\\", "/") }))
    .sort((left, right) => {
      if (left.name < right.name) return -1;
      if (left.name > right.name) return 1;
      return 0;
    });
  for (const { path, name } of ordered) {
    digest.update(name, "utf8");
    digest.update(Buffer.from([0]));
    digest.update(readFileSync(path));
    digest.update(Buffer.from([0]));
  }
  return digest.digest("hex").slice(0, 12);
}

const backendDigest = sourceDigest(backendFiles, projectRoot);
const frontendDigest = sourceDigest(frontendCandidates, join(projectRoot, "webui"));
const buildStateOutput = join(projectRoot, "webui", "dist", "build-state.json");
const writeBuildState = {
  name: "write-build-state",
  closeBundle() {
    mkdirSync(dirname(buildStateOutput), { recursive: true });
    writeFileSync(
      buildStateOutput,
      JSON.stringify({ backend: backendDigest, frontend: frontendDigest }, null, 2),
      "utf8",
    );
  },
};

export default defineConfig({
  plugins: [vue(), writeBuildState],
  base: "/static/",
  define: {
    __EXPECTED_BACKEND_BUILD_HASH__: JSON.stringify(backendDigest),
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
