#!/usr/bin/env bash
# Career Scout macOS DMG 构建脚本。
#
# 前置校验前端 dist 与打包依赖，调用 PyInstaller 按
# packaging/career_scout_macos.spec 构建 CareerScout.app，
# 再用 hdiutil 打成 .release/CareerScout-v{version}.dmg。
# 任一步失败非零退出；成功输出产物绝对路径。
#
# 在 macOS 的项目根目录执行：bash packaging/build_dmg.sh

set -euo pipefail

# 项目根（脚本在 packaging/ 下，父目录即项目根）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

if [[ "$(uname)" != "Darwin" ]]; then
    echo "错误：本脚本只能在 macOS 上运行（PyInstaller 不支持跨平台编译）" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# 1. 从 pyproject.toml 读版本
# ---------------------------------------------------------------------------
VERSION="$(sed -n 's/^version *= *"\([^"]*\)".*/\1/p' pyproject.toml | head -n1)"
if [[ -z "$VERSION" ]]; then
    echo "错误：pyproject.toml 未找到 version 字段" >&2
    exit 1
fi
echo "Career Scout 版本：$VERSION"

# ---------------------------------------------------------------------------
# 2. 前端构建（产物不入库，发布前始终用最新源码现场构建）
# ---------------------------------------------------------------------------
echo '使用最新源码构建前端（产物不入库，发布前现场构建）...'
(
    cd webui
    if [[ ! -d 'node_modules' ]]; then
        echo '> npm ci'
        npm ci
    fi
    echo '> npm run build'
    npm run build
)
if [[ ! -f 'webui/dist/index.html' ]]; then
    echo "错误：前端构建后仍无 webui/dist/index.html" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# 3. 打包依赖校验（缺失则自动安装）
# ---------------------------------------------------------------------------
if ! uv run python -c 'import PyInstaller, webview' 2>/dev/null; then
    echo '打包依赖缺失，执行安装：pyinstaller pywebview pyobjc-framework-Cocoa pyobjc-framework-WebKit'
    uv pip install pyinstaller pywebview pyobjc-framework-Cocoa pyobjc-framework-WebKit
fi

# ---------------------------------------------------------------------------
# 3.5 生成 .app 图标：iconutil 把 icon.iconset 转 career_scout.icns
# ---------------------------------------------------------------------------
ICONSET_DIR='packaging/assets/icon.iconset'
ICNS_PATH='packaging/assets/career_scout.icns'
if [[ -d "$ICONSET_DIR" ]]; then
    if command -v iconutil >/dev/null 2>&1; then
        echo '> iconutil 生成 career_scout.icns'
        iconutil -c icns "$ICONSET_DIR" -o "$ICNS_PATH"
    else
        echo '警告：未找到 iconutil，.app 将使用默认图标' >&2
    fi
fi

# ---------------------------------------------------------------------------
# 4. PyInstaller 构建 .app
# ---------------------------------------------------------------------------
echo '> uv run pyinstaller packaging/career_scout_macos.spec --noconfirm'
uv run pyinstaller packaging/career_scout_macos.spec --noconfirm

if [[ ! -d 'dist/CareerScout.app' ]]; then
    echo "错误：构建完成但未找到 dist/CareerScout.app" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# 5. hdiutil 打 DMG
# ---------------------------------------------------------------------------
mkdir -p .release
DMG_NAME="CareerScout-v${VERSION}.dmg"
DMG_PATH="$PROJECT_ROOT/.release/$DMG_NAME"
STAGING_DIR="$(mktemp -d)"
trap 'rm -rf "$STAGING_DIR"' EXIT

cp -R 'dist/CareerScout.app' "$STAGING_DIR/"
ln -s /Applications "$STAGING_DIR/Applications"

rm -f "$DMG_PATH"
hdiutil create \
    -volname "CareerScout" \
    -srcfolder "$STAGING_DIR" \
    -ov \
    -format UDZO \
    "$DMG_PATH"

# 清理 PyInstaller 中间目录（build/、dist/ 已被 .gitignore 忽略）
rm -rf build dist

echo "构建成功：$DMG_PATH"
echo "$DMG_PATH"
