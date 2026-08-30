# 契约：发布与验证流水线

**适用**：批次 1（暴露面收敛）与批次 2（发布验证闭环）。

## CI（ci.yml）

| 作业 | 运行器 | 步骤 |
|---|---|---|
| test（既有，保持） | ubuntu-latest | setup-python 3.11 → uv sync → `uv run python -m unittest discover -s tests` → npm ci → `npm test` |
| test-windows（新增） | windows-latest | 同构步骤；GitHub 托管 Windows 运行器自带 Chrome，tests/source、tests/chrome_setup 直接可跑 |

## 发布（release-macos.yml）

新增构建前测试 job：`needs: test-gate`，测试不过不进入构建与上传。既有自校验（hdiutil verify、lipo、版本断言、SHA256、镜像同步重试+远端哈希复核）全部保留。

## 配置项（替换明文）

| 项 | 来源 | 说明 |
|---|---|---|
| 镜像地址 | `vars.MIRROR_HOST` | 14 处明文清零 |
| 部署账号 | `vars.MIRROR_USER` | 默认非 root；用户在服务器建账号后填入 |
| 远端目录 | `vars.MIRROR_PATH` | 现 `/var/www/career-scout` |
| 部署私钥 | `secrets.MIRROR_SSH_KEY` | 既有不变 |
| 主机指纹 | `secrets.MIRROR_KNOWN_HOSTS` | 用户提供（操作单 D-1），替换运行时 `ssh-keyscan`（TOFU 加固） |
| 缺配置行为 | — | 镜像同步步骤显式跳过并在流水线输出醒目提示（沿用现有 `if: env.MIRROR_SSH_KEY != ''` 模式扩展到 vars） |

## 标签纪律（release_check.ps1）

- 新增检查：读 CHANGELOG 首个 `## [x.y.z]`，`git rev-parse --verify refs/tags/vx.y.z` 必须成功；缺失 → 报错"版本与标签脱节"。
- `-SkipTagCheck` 开关：显式豁免时输出醒目提示（供"先提交后打标"的中间态使用）。
- 现有检查（版本一致性、dist 同步、卫生、whitespace、产物在场）全部保留。

## 服务器侧操作单（交付物，代码够不着）

用户执行的命令清单（建非 root 账号、授权目录、固化指纹、可选换 IP）作为独立交付文档随批次 1 产出：`roadmap/server-ops-031.md`（roadmap 为本地专用目录、已被 .gitignore，不入 git——放 specs/ 会成为未跟踪文件被卫生测试拦截）。
