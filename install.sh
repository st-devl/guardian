#!/bin/sh
# Bir kerelik: `guardian` komutunu ~/.local/bin altına bağlar (macOS zsh ve Linux).
set -eu
src="$(cd "$(dirname "$0")" && pwd)/guardian.py"
python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null \
  || { echo "Python 3.11+ gerekli (python3 --version)"; exit 1; }
mkdir -p "$HOME/.local/bin"
chmod +x "$src"
ln -sf "$src" "$HOME/.local/bin/guardian"
echo "guardian -> $src"
case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *) echo "PATH'e ekleyin: echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.$(basename "${SHELL:-sh}")rc && exec \$SHELL" ;;
esac
