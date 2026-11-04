#!/usr/bin/env bash
# Task Board 설치 — board 명령 · 서버(사용자 서비스, 재부팅 후 자동) · board 스킬 · 전역 CLAUDE.md 규칙.
# 여러 번 돌려도 결과가 같다. 설정은 ~/.config/board/env (처음 한 번만 만든다).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"

# 플러그인으로 안 깔고 이 저장소를 직접 쓰는 PC 용 — 스킬 링크와 전역 CLAUDE.md 규칙까지 넣는다.
# (플러그인으로 깔면 스킬은 플러그인이, 규칙은 세션 시작 훅이 넣으니 이건 필요 없다 — 둘 다 하면 규칙이 두 번 들어간다.)
mkdir -p "$HOME/.claude/skills"
ln -sfn "$here/skills/board" "$HOME/.claude/skills/board"
python3 "$here/board.py" setup

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
systemctl --user is-active --quiet board.service && echo "Task Board 설치 완료 — $(python3 "$here/board.py" url)" || { echo "서버가 안 떴습니다: journalctl --user -u board -n 20"; exit 1; }
