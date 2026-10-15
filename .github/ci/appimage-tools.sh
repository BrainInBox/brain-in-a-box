#!/usr/bin/env bash
# The tools tauri-bundler downloads to build an AppImage, from branches and "continuous" releases
# it never checks: fetched here instead, from pinned addresses, each checked against its SHA-256,
# into the folder tauri-bundler takes them from (~/.cache/tauri). It then downloads nothing.
# Run before `cargo tauri build --bundles appimage` (release.yml, test.yml, terrain.yml), x86-64.
# A new pin: the file's address at a commit or a tagged release, and `sha256sum` of it.
set -euo pipefail

dir="${XDG_CACHE_HOME:-$HOME/.cache}/tauri"
mkdir -p "$dir"
while read -r name sum url; do
  [ -n "$name" ] || continue
  part="$dir/.$name.part"
  curl -q -fsSL --proto =https --proto-redir =https --retry 3 -o "$part" "$url"
  got=$(sha256sum "$part" | cut -d' ' -f1)
  if [ "$got" != "$sum" ]; then
    rm -f "$part"
    echo "::error::$name: SHA-256 $got, pinned $sum ($url)"
    exit 1
  fi
  chmod 755 "$part"
  mv -f "$part" "$dir/$name"
  echo "$name: $sum"
done <<'PINS'
AppRun-x86_64 f30140a43a0a59e46db21bdefdf749b9e9f2c6946e92afabbacf98b8ae73fb4f https://github.com/tauri-apps/binary-releases/releases/download/apprun-old/AppRun-x86_64
linuxdeploy-x86_64.AppImage e762bea85c8eb0d4b3508d46e5c1f037f717d0f9303ae3b4aafc8b04991fa1ef https://github.com/tauri-apps/binary-releases/releases/download/linuxdeploy/linuxdeploy-x86_64.AppImage
linuxdeploy-plugin-gtk.sh 7804c9eef13e59bf2783aad9882ef9db8f3f3f9e8d631874b1d348d550a3693f https://raw.githubusercontent.com/tauri-apps/linuxdeploy-plugin-gtk/dda522bce37387f1b853d9095713bfaa924c8423/linuxdeploy-plugin-gtk.sh
linuxdeploy-plugin-gstreamer.sh c107b49d84edbffc6ab226ed1007e0626a4f7aa2c3a36b7782bef62351d49e94 https://raw.githubusercontent.com/tauri-apps/linuxdeploy-plugin-gstreamer/2a2e67491c32995a3f279ad0ecbe77abd512b42a/linuxdeploy-plugin-gstreamer.sh
linuxdeploy-plugin-appimage.AppImage 992d502a248e14ab185448ddf6f6e7d25558cb84d4623c354c3af350c25fccb3 https://github.com/linuxdeploy/linuxdeploy-plugin-appimage/releases/download/1-alpha-20250213-1/linuxdeploy-plugin-appimage-x86_64.AppImage
PINS
