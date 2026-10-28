#!/usr/bin/env bash
# 安装 krunner-ai —— 全部安装到用户家目录，不触碰系统目录，不需要 root。
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

BIN_SRC="$SRC/src/krunner_ai/daemon.py"
BIN_DST="$HOME/.local/bin/krunner-ai-daemon"

echo "==> 安装守护服务"
install -Dm755 "$BIN_SRC" "$BIN_DST"
install -Dm755 "$SRC/src/krunner_ai/chat_window.py" "$HOME/.local/bin/krunner-ai-chat"

echo "==> 注册 KRunner DBus 插件"
install -Dm644 "$SRC/data/dev.local.krunnerai.desktop" \
  "$HOME/.local/share/krunner/dbusplugins/dev.local.krunnerai.desktop"

echo "==> 安装 systemd 用户服务"
install -Dm644 "$SRC/data/krunner-ai.service" \
  "$HOME/.config/systemd/user/krunner-ai.service"

echo "==> 安装图标"
install -Dm644 "$SRC/data/dev.local.krunnerai.svg" \
  "$HOME/.local/share/icons/hicolor/scalable/apps/dev.local.krunnerai.svg"

mkdir -p -m 700 "$HOME/.cache/krunner-ai"

if [ ! -f "$HOME/.config/krunner-ai/config.toml" ]; then
  echo "==> 生成配置文件（0600）"
  install -Dm600 "$SRC/data/config.example.toml" "$HOME/.config/krunner-ai/config.toml"
  echo "    !! 请编辑 ~/.config/krunner-ai/config.toml 填入两个 API key"
fi

echo "==> 启动服务"
systemctl --user daemon-reload
systemctl --user enable krunner-ai.service >/dev/null 2>&1 || true
systemctl --user restart krunner-ai.service

echo "==> 重启 KRunner 以发现插件"
if systemctl --user is-active plasma-krunner.service >/dev/null 2>&1; then
  systemctl --user restart plasma-krunner.service
fi

sleep 1.5
systemctl --user is-active krunner-ai.service && echo "    服务运行中 ✓"

"$BIN_DST" selftest

cat <<'EOF'

安装完成。下一步：
  1. 注册智谱 BigModel（免费，无需信用卡）:       https://bigmodel.cn
  2. 注册 Tavily 并创建免费搜索 key:             https://app.tavily.com
  3. 智谱控制台「API Keys」创建 key，
     与 Tavily key 一起填入 ~/.config/krunner-ai/config.toml
  4. systemctl --user restart krunner-ai
  5. 按 Alt+Space 唤出 KRunner，输入:  ai ping
EOF
