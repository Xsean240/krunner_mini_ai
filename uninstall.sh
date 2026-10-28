#!/usr/bin/env bash
# 卸载 krunner-ai。默认保留 ~/.config/krunner-ai（内含 API key）；
# 加 --purge 一并删除配置。
set -euo pipefail

echo "==> 停止并禁用服务"
systemctl --user disable --now krunner-ai.service 2>/dev/null || true

echo "==> 删除文件"
rm -f "$HOME/.local/bin/krunner-ai-daemon"
rm -f "$HOME/.local/bin/krunner-ai-chat"
rm -f "$HOME/.local/share/krunner/dbusplugins/dev.local.krunnerai.desktop"
rm -f "$HOME/.config/systemd/user/krunner-ai.service"
rm -f "$HOME/.local/share/icons/hicolor/scalable/apps/dev.local.krunnerai.svg"
rm -rf "$HOME/.cache/krunner-ai"
systemctl --user daemon-reload

if [ "${1:-}" = "--purge" ]; then
  rm -rf "$HOME/.config/krunner-ai"
  echo "==> 已删除配置（含 API key）"
fi

echo "==> 重启 KRunner"
if systemctl --user is-active plasma-krunner.service >/dev/null 2>&1; then
  systemctl --user restart plasma-krunner.service
fi

echo "卸载完成。"
