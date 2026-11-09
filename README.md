# Task Board (board)

Claude Code 세션을 여러 개 돌리면 각 세션이 묻는 것이 채팅 스크롤에 묻힌다.
Task Board 는 세션들이 **물을 것·진행 보고를 작업별 문서로 올리고**, 사람은 **브라우저 탭 하나씩** 보면서 답·메모·이미지를 넣는다.
[보내기]를 누르면 그 세션이 알아서 깨어나 읽고 반영한다.

- 탭마다 "내 차례" 숫자 — 어느 세션이 내 답을 기다리는지 한눈에
- 질문 카드 · 답 칸 · 이미지 붙여넣기 · 노드·엣지 그림(mermaid) · 자유 메모
- 진행 중 / 끝난 작업 두 갈래
- 문서는 그냥 마크다운 파일 — 편집기로 열어도 된다

## 설치

Claude Code 플러그인으로 깐다(gh 로그인·Claude Code 로그인 가정).

```bash
claude plugin marketplace add glowElephant/task-board
claude plugin install task-board@task-board
```

그다음 아무 세션에서 "Task Board 설정해 줘" — 세션이 `board setup` 을 돌리고, 밖에서 볼 목록 아티팩트를 한 번 만든다.

- 리눅스(systemd)면 PC 화면 http://localhost:8770 이 사용자 서비스로 뜬다. 맥·윈도우는 아티팩트만(PC 화면은 수동 `python3 board.py serve`).
- 플러그인 없이 쓰려면 `git clone` 뒤 `bash install.sh`(스킬 링크 + 전역 CLAUDE.md 규칙 + setup). 둘 다 하지 말 것 — 규칙이 두 번 들어간다.
- 설정 `~/.config/board/env`: `BOARD_DIR`, `BOARD_PORT`, `BOARD_HUB`(목록 아티팩트), `BOARD_PC`(기본 hostname), `BOARD_AWAY_MIN`.

**[보내기]를 바로 받기(채널, 리눅스)** — `board setup` 이 MCP `board` 를 등록한다. 세션을 `claude --dangerously-load-development-channels server:board` 로 띄우면
세션이 아무것도 걸어 두지 않아도 [보내기] 때 깨어난다(Claude Code Channels, 연구 미리보기 — 띄울 때마다 경고 창 한 번).
매번 치기 싫으면 `bin/claude` 래퍼를 PATH 맨 앞에 — setup 이 넣을 줄을 찍어 준다.

필요한 것: python3 3.9+, Claude Code(아티팩트 기능). 화면은 인터넷에서 marked·mermaid 를 불러온다.

## 세션이 쓰는 법

```bash
board new <작업> --title "제목" --session "세션 이름 [ref]"   # 문서 만들기
board wait <작업>          # 채널 없는 세션만 — [보내기]까지 대기(Bash run_in_background)
board done <작업>          # 끝난 작업으로
board list                 # 목록
board push <작업>          # 밖에서 보는 아티팩트에도 올림(찍힌 순서대로 도구 호출)
```

문서 형식과 절차(아티팩트 push 포함)는 [`skills/board/SKILL.md`](skills/board/SKILL.md).
