# Task Board (board)

Claude Code 세션을 여러 개 돌리면 각 세션이 묻는 것이 채팅 스크롤에 묻힌다.
Task Board 는 세션들이 **물을 것·진행 보고를 작업별 문서로 올리고**, 사람은 **브라우저 탭 하나씩** 보면서 답·메모·이미지를 넣는다.
[보내기]를 누르면 그 세션이 알아서 깨어나 읽고 반영한다.

- 탭마다 "내 차례" 숫자 — 어느 세션이 내 답을 기다리는지 한눈에
- 질문 카드 · 답 칸 · 이미지 붙여넣기 · 노드·엣지 그림(mermaid) · 자유 메모
- 진행 중 / 끝난 작업 두 갈래 · 작업이 끝나면 PC 화면 위에 배너(세션마다 이모지·색이 다름, 리눅스 X11) · 🔔 를 켠 작업은 슬랙 DM 도 한 번
- [＋ 새 작업] — 보드에서 세션을 골라 제목·할 일을 적으면 그 세션이 바로 깨어나 그 문서에서 일한다(채널)
- 폰 화면 — 자기 도메인(Cloudflare 터널)으로 밖에서도, 로그인은 슬랙으로 받은 코드(기기마다 3달)
- 문서는 그냥 마크다운 파일 — 편집기로 열어도 된다

## 설치

Claude Code 플러그인으로 깐다(gh 로그인·Claude Code 로그인 가정).

```bash
claude plugin marketplace add glowElephant/task-board
claude plugin install task-board@task-board
```

그다음 아무 세션에서 "Task Board 설정해 줘" — 세션이 `board setup` 을 돌린다.

- 리눅스(systemd)면 PC 화면 http://localhost:8770 이 사용자 서비스로 뜬다. 맥·윈도우는 수동 `python3 board.py serve`.
- 플러그인 없이 쓰려면 `git clone` 뒤 `bash install.sh`(스킬 링크 + 전역 CLAUDE.md 규칙 + setup). 둘 다 하지 말 것 — 규칙이 두 번 들어간다.
- 설정 `~/.config/board/env`: `BOARD_DIR`, `BOARD_PORT`, 밖에서 보기 `SLACK_BOT_TOKEN`·`BOARD_SLACK_EMAIL`(또는 멤버 ID `BOARD_SLACK_USER` — 봇에 메일 찾기 권한이 없을 때)(둘 다 있으면 `127.0.0.1:BOARD_PUBLIC_PORT`(기본 8771)에 로그인이 필요한 문이 열린다), `BOARD_PUBLIC_URL`(슬랙 알림 링크).

**밖(폰)에서 보기** — 밖의 문에 Cloudflare 터널을 붙인다(공유기 포트를 안 연다, https 는 Cloudflare 가).
```bash
cloudflared tunnel login                         # 브라우저에서 도메인 고르기(한 번)
cloudflared tunnel create board
cloudflared tunnel route dns board board.<도메인>
# ~/.cloudflared/board.yml: tunnel·credentials-file·ingress(hostname board.<도메인> → http://127.0.0.1:8771, 나머지 http_status:404)
cloudflared tunnel --config ~/.cloudflared/board.yml run   # 사용자 서비스로 걸어 둔다
```
슬랙 봇은 `chat:write` 가 있어야 한다. 메일로 찾으려면 `users:read.email` 도(없으면 `BOARD_SLACK_USER`).

**[보내기]를 바로 받기(채널, 리눅스)** — `board setup` 이 MCP `board` 를 등록한다. 세션을 `claude --dangerously-load-development-channels server:board` 로 띄우면
세션이 아무것도 걸어 두지 않아도 [보내기] 때 깨어난다(Claude Code Channels, 연구 미리보기 — 띄울 때마다 경고 창 한 번).
매번 치기 싫으면 `bin/claude` 래퍼를 PATH 맨 앞에 — setup 이 넣을 줄을 찍어 준다.

필요한 것: python3 3.9+, Claude Code. 화면은 인터넷에서 marked·mermaid 를 불러온다.

## 세션이 쓰는 법

```bash
board new <작업> --title "제목" --session "세션 이름 [ref]"   # 문서 만들기
board wait <작업>          # 채널 없는 세션만 — [보내기]까지 대기(Bash run_in_background)
board done <작업>          # 끝난 작업으로
board list                 # 목록
```

문서 형식과 절차는 [`skills/board/SKILL.md`](skills/board/SKILL.md).
