#!/bin/bash
# クラウドセッション（Claude Code on the web / Routine）の開始時に、
# テスト用の pytest を入れる。本体は標準ライブラリだけで動くので、入れるのはこれだけ。
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR"
python -m pip install --quiet --disable-pip-version-check --root-user-action=ignore -r requirements.txt
