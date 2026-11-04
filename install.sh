#!/usr/bin/env bash
# Task Board 설치 — board 명령 · 서버(사용자 서비스, 재부팅 후 자동) · board 스킬 · 전역 CLAUDE.md 규칙.
# 여러 번 돌려도 결과가 같다. 설정은 ~/.config/board/env (처음 한 번만 만든다).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"

bin="${XDG_BIN_HOME:-$HOME/.local/bin}"
mkdir -p "$bin" "$HOME/.claude/skills"
chmod +x "$here/board.py"
ln -sfn "$here/board.py" "$bin/board"
ln -sfn "$here/skill" "$HOME/.claude/skills/board"

cfg="${XDG_CONFIG_HOME:-$HOME/.config}/board"
mkdir -p "$cfg"
[ -f "$cfg/env" ] || printf 'BOARD_DIR=%s\nBOARD_PORT=8770\n# 옛 txt 문서 폴더(탭으로 보여 주기만) — 여럿이면 : 로\nBOARD_EXTRA=\n' "$HOME/board" > "$cfg/env"

unit="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/board.service"
mkdir -p "$(dirname "$unit")"
cat > "$unit" <<EOF
[Unit]
Description=board (Task Board)

[Service]
ExecStart=/usr/bin/env python3 $here/board.py serve
Restart=on-failure

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable board.service >/dev/null
systemctl --user restart board.service

md="$HOME/.claude/CLAUDE.md"
touch "$md"
python3 - "$md" "$here/claude-rule.md" <<'PY'
import re, sys
md, rule = sys.argv[1], open(sys.argv[2], encoding="utf-8").read().strip()
s = open(md, encoding="utf-8").read()
block = "<!-- board:begin (personal/board/install.sh 가 관리 — 고치려면 claude-rule.md) -->\n" + rule + "\n<!-- board:end -->"
pat = re.compile(r"<!-- board:begin.*?<!-- board:end -->", re.S)
s = pat.sub(lambda m: block, s) if pat.search(s) else s.rstrip() + "\n\n" + block + "\n"
open(md, "w", encoding="utf-8").write(s)
PY

sleep 1
systemctl --user is-active --quiet board.service && echo "Task Board 설치 완료 — $("$bin/board" url)" || { echo "서버가 안 떴습니다: journalctl --user -u board -n 20"; exit 1; }
