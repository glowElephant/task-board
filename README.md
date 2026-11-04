# Task Board (board)

Claude Code 세션을 여러 개 돌리면 각 세션이 묻는 것이 채팅 스크롤에 묻힌다.
Task Board 는 세션들이 **물을 것을 작업별 문서로 올리고**, 사람은 **브라우저 탭 하나씩** 보면서 답·메모·이미지를 넣는다.
[보내기]를 누르면 그 세션이 알아서 깨어나 읽고 반영한다.

- 탭마다 "내 차례" 숫자 — 어느 세션이 내 답을 기다리는지 한눈에
- 질문 카드 · 답 칸 · 이미지 붙여넣기 · 노드·엣지 그림(mermaid) · 자유 메모
- 진행 중 / 끝난 작업 두 갈래
- 문서는 그냥 마크다운 파일 — 편집기로 열어도 된다

## 설치

```bash
git clone <이 저장소> && cd board && bash install.sh
```

`board` 명령, 사용자 서비스(재부팅 후 자동 실행), Claude Code `board` 스킬, `~/.claude/CLAUDE.md` 규칙까지 들어간다.
화면: http://localhost:8770 · 설정: `~/.config/board/env` (`BOARD_DIR`, `BOARD_PORT`, `BOARD_EXTRA`)

필요한 것: Linux(systemd 사용자 세션), python3 3.9+, Claude Code. 화면은 인터넷에서 marked·mermaid 를 불러온다.

## 세션이 쓰는 법

```bash
board new <작업> --title "제목" --session "세션 이름 [ref]"   # 문서 만들기
board wait <작업>          # [보내기]까지 대기 — Bash run_in_background 로
board done <작업>          # 끝난 작업으로
board list                 # 목록
```

문서 형식과 절차는 [`skill/SKILL.md`](skill/SKILL.md).
