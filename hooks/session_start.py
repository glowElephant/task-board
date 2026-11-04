#!/usr/bin/env python3
"""플러그인 세션 시작 훅 — Task Board 규칙(claude-rule.md)을 세션 맥락에 넣는다. 전역 CLAUDE.md 를 고치지 않고도 모든 세션이 규칙을 안다.
처음 설정 전이면 setup 을, 목록 아티팩트가 없으면 만드는 법을 한 줄 덧붙인다."""
import os
import pathlib
import shutil

root = pathlib.Path(__file__).resolve().parent.parent
print((root / "claude-rule.md").read_text(encoding="utf-8").strip())
cfg = pathlib.Path(os.environ.get("XDG_CONFIG_HOME") or pathlib.Path.home() / ".config") / "board" / "env"
lines = cfg.read_text(encoding="utf-8").splitlines() if cfg.is_file() else []
hub = any(ln.startswith("BOARD_HUB=") and ln.split("=", 1)[1].strip() for ln in lines)
if not shutil.which("board") or not lines:
    print(f"\n(Task Board 처음 설정 전 — 사용자에게 물을 일이 생기면 먼저 `python3 {root}/board.py setup` 을 실행하고 찍힌 안내대로 한다.)")
elif not hub:
    print("\n(Task Board 목록 아티팩트가 아직 없다 — `board setup` 이 찍는 안내대로 한 번 만들면 밖(폰)에서도 보인다.)")
