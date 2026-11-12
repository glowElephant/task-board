#!/usr/bin/env python3
"""플러그인 세션 시작 훅 — Task Board 규칙(claude-rule.md)을 세션 맥락에 넣는다. 전역 CLAUDE.md 를 고치지 않고도 모든 세션이 규칙을 안다.
처음 설정 전이면 setup 을 한 줄 덧붙인다."""
import os
import pathlib
import shutil

root = pathlib.Path(__file__).resolve().parent.parent
print((root / "claude-rule.md").read_text(encoding="utf-8").strip())
cfg = pathlib.Path(os.environ.get("XDG_CONFIG_HOME") or pathlib.Path.home() / ".config") / "board" / "env"
lines = cfg.read_text(encoding="utf-8").splitlines() if cfg.is_file() else []
if not shutil.which("board") or not lines:
    print(f"\n(Task Board 처음 설정 전 — 사용자에게 물을 일이 생기면 먼저 `python3 {root}/board.py setup` 을 실행하고 찍힌 안내대로 한다.)")
