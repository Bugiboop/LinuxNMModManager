#!/usr/bin/env bash
# build_release.sh — builds ModManager and packages a distributable zip.
# Usage: ./build_release.sh [version]   (default: 1.0.0)
set -euo pipefail

VERSION="${1:-1.0.0}"
DIST_NAME="ModManager-${VERSION}-linux"
RELEASE_DIR="dist/${DIST_NAME}"
VENV=".venv/bin"

echo "=== Building ModManager ${VERSION} ==="

# ── 0. Ensure PyInstaller is available ─────────────────────────────────────
if ! "${VENV}/python" -c "import PyInstaller" 2>/dev/null; then
    echo "[setup] Installing PyInstaller into .venv …"
    "${VENV}/pip" install pyinstaller
fi

# ── 1. Clean previous build artefacts ──────────────────────────────────────
echo "[clean] Removing old build artefacts …"
rm -rf build/ dist/ModManager "dist/${DIST_NAME}" "${DIST_NAME}.zip"

# ── 2. Run PyInstaller ─────────────────────────────────────────────────────
echo "[build] Running PyInstaller …"
"${VENV}/pyinstaller" ModManager.spec

# ── 3. Assemble the release directory ──────────────────────────────────────
echo "[package] Assembling ${RELEASE_DIR} …"
mkdir -p "${RELEASE_DIR}"

# Executable
cp dist/ModManager "${RELEASE_DIR}/ModManager"
chmod +x "${RELEASE_DIR}/ModManager"

# game_profiles — copy only the profile JSON + create empty data dirs
# Skip any game directory that has no matching <id>.json (e.g. palworld stub)
for profile_dir in game_profiles/*/; do
    game_id="$(basename "${profile_dir}")"
    json="${profile_dir}${game_id}.json"

    if [ ! -f "${json}" ]; then
        echo "[skip]  game_profiles/${game_id}/ has no profile JSON, skipping"
        continue
    fi

    dest="${RELEASE_DIR}/game_profiles/${game_id}"
    mkdir -p "${dest}/compressed" "${dest}/compressed-disabled" "${dest}/mods"
    cp "${json}" "${dest}/"
    echo "[copy]  game_profiles/${game_id}/${game_id}.json"
done

# Template config.json — game_root left blank for the user to fill in
cat > "${RELEASE_DIR}/config.json" << 'JSON'
{
  "current_game": "stellar_blade",
  "games": {
    "stellar_blade": {
      "game_root": "",
      "nexus_api_key": ""
    }
  },
  "theme": "dark"
}
JSON

# ── 4. Zip the release ─────────────────────────────────────────────────────
echo "[zip]   Creating ${DIST_NAME}.zip …"
(cd dist && zip -r "../${DIST_NAME}.zip" "${DIST_NAME}")

echo ""
echo "=== Done! ==="
echo "    Release zip : ${DIST_NAME}.zip"
echo "    Size        : $(du -sh "${DIST_NAME}.zip" | cut -f1)"
echo ""
echo "NOTE: Users still need 'p7zip-full' (sudo apt install p7zip-full)"
echo "      to extract .rar and .7z archives. .zip works out of the box."
