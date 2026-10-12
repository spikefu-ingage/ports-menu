#!/bin/zsh
# Symlink the plugin into SwiftBar's plugin folder (read from SwiftBar's prefs).
# SwiftBar runs EVERY file in its plugin folder, so that folder must be a dedicated
# one — never this repo. If SwiftBar currently points at this repo (or at nothing),
# point it at ~/Library/Application Support/SwiftBar/Plugins and restart it.
set -e
here=$(cd "$(dirname "$0")" && pwd)
plugin_dir=$(defaults read com.ameba.SwiftBar PluginDirectory 2>/dev/null || true)
plugin_dir=${plugin_dir/#\~/$HOME}
if [[ -z "$plugin_dir" || "$plugin_dir" -ef "$here" ]]; then
  plugin_dir="$HOME/Library/Application Support/SwiftBar/Plugins"
  mkdir -p "$plugin_dir"
  defaults write com.ameba.SwiftBar PluginDirectory "$plugin_dir"
  echo "Pointed SwiftBar's plugin folder at: $plugin_dir"
  restart=1
fi
chmod +x "$here/ports-menu.10s.py"
ln -sf "$here/ports-menu.10s.py" "$plugin_dir/"
echo "Linked $here/ports-menu.10s.py -> $plugin_dir/"
if [[ -n "$restart" ]] && pgrep -xq SwiftBar; then
  killall SwiftBar && sleep 1 && open -a SwiftBar
  echo "Restarted SwiftBar."
fi
