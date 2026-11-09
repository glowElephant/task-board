# board (Task Board, 옛 이름 확인판)
여러 Claude Code 세션이 사용자에게 물을 것(판단·결정·승인·알림)을 작업별 문서로 모아 브라우저 탭 하나씩 보여 주는 로컬 도구. 채팅 스크롤에 질문이 묻히는 문제를 없앤다.

## 현재 상태
- 2026-11-03 1단계 완료: 서버·화면·CLI·스킬·전역 CLAUDE.md 규칙, `install.sh` 로 설치(이 PC 설치됨, 사용자 서비스 `board`).
- [보내기] → `board wait` 종료 → 세션 깨움 왕복은 자가 시험으로 확인(2026-11-03).
- 2026-11-03 가로 탭 → 왼쪽 세션별 접히는 목록(작업마다 내 차례 질문 머리 미리보기) — 사용자 "눌러봐야 뭔지 아니까", 작업 17개가 되자 탭이 잘림. 접힘 상태는 localStorage.
- 2026-11-03 데스크톱 알림 추가(사용자 Q3 선택): 내 차례가 새로 생기면 GNOME 알림, 누르면 그 작업 화면.
- 2026-11-04 이름 Task Board(사용자). 화면·알림·문서에 보이는 이름만 — 명령어 `board`·스킬 이름은 그대로(떠 있는 세션이 다시 배울 일 없게).
- 2026-11-04 밖에서 보기: 세션마다 아티팩트 한 장(`board push`) + 목록 아티팩트 한 장(`board hubpage`, db `tasks`). 목록 주소는 각자 설정 `BOARD_HUB`.
- 2026-11-04 플러그인 모양(`.claude-plugin/`, `skills/board/`, `hooks/`) + `board setup` — 다른 사람·다른 PC 는 플러그인 설치 두 줄 + setup.
  저장소 github.com/glowElephant/task-board — 2026-11-04 공개(사용자 결정: 공개하되 회사 안에만 알림). 빈 홈에서 `claude plugin marketplace add glowElephant/task-board` → install 시험 통과.
- 2026-11-09 채널: [보내기]를 세션에 바로 넣는다(`board wait` 불필요). 시험 세션으로 확인 — 쉬는 세션이 깨어나 반영·답(8초), 한 번만, sid 없으면 이름으로, 서버 재시작 3초 뒤 재연결·재배달.
  이 PC: user MCP `board` 등록(`board setup`), 래퍼 `bin/claude`. PATH 줄(~/.bashrc·~/.profile)은 사용자가 넣는다(자동 모드가 셸 시작 파일 수정을 막음).
  채널 없이 뜬 세션은 다시 띄울 때까지 `board wait` 그대로 — 둘이 섞여도 된다. 같은 날 Orca 세션 15개 전부 채널로 다시 띄움.
- 2026-11-09 **모든 작업을 보드로**(사용자 "이제 모든 작업은 테스크보드로", board-everything Q1~Q2=A) — 물을 것만이 아니라 진행·완료 보고·결과 요약까지 작업 문서에, 채팅은 한 줄. 전 세션.
- 2026-11-09 세션 아티팩트 묶음 키 ref → sid(board-everything Q3=A). 열린 작업 sid 채움·옛 ref 기록(주소) 옮김.

## 구조
```
board.py               서버 + CLI 한 파일(파이썬 표준 라이브러리만). 문서 해석·고쳐 넣기·push·setup 도 여기
web/index.html         화면 한 장 — PC 화면 · 세션 아티팩트(mode=session) · 목록 아티팩트(mode=hub) 셋 다 이 코드
skills/board/SKILL.md  세션용 절차·형식(플러그인 스킬 · install.sh 는 ~/.claude/skills/board 로 링크)
claude-rule.md         세션이 늘 알아야 할 규칙 — 플러그인은 세션 시작 훅(hooks/session_start.py)이, install.sh 는 전역 CLAUDE.md 에 넣는다
.claude-plugin/        plugin.json · marketplace.json — `claude plugin marketplace add glowElephant/task-board`
install.sh             플러그인 없이 직접 쓰는 PC 용(스킬 링크 · 전역 규칙 · board setup)
bin/claude             대화형 claude 에만 채널 옵션을 붙이는 래퍼(PATH 에서 ~/.local/bin 보다 앞)
test_board.py          해석·고쳐 넣기 자가 점검
```
데이터: `$BOARD_DIR/<작업>/task.md`(+`img/`, `.sent`=보낸 신호, `.seen`=세션이 읽은 표시, `.pushed`=자리 비움 폰 알림 보낸 표시). 설정 `~/.config/board/env`.
문서 머리 `sid:` = 그 작업을 받을 세션 id(`board new`·`wait`·`push` 가 `$CLAUDE_CODE_SESSION_ID` 로 적는다).

## 실행/빌드/테스트
```bash
python3 test_board.py                 # "ok" 면 통과
bash install.sh                       # 설치·갱신(서버 재시작 포함)
systemctl --user restart board        # board.py 만 고쳤을 때
journalctl --user -u board -n 30      # 서버 로그
board list; board url <작업>
```
화면 확인: `google-chrome --headless=new --screenshot=<png> --window-size=1440,1400 --virtual-time-budget=6000 http://localhost:8770/#<작업>` — 창이 안 떠서 사용자 작업을 방해하지 않는다.

## 설계 결정
- 파일이 원본, 서버는 상태 없음 — 서버가 죽어도 세션·gedit 로 계속 일할 수 있고, 세션은 Read/Edit 로 바로 다룬다.
- 화면 저장은 그 질문의 답 칸·메모만 그 자리에 고쳐 넣는다(파일 통째 덮기 X) — 세션이 같은 문서의 다른 곳을 고쳐도 안 부딪힌다. 원문 편집만 mtime 비교로 충돌을 거절.
- 깨우기 = `board wait` 를 세션이 Bash 백그라운드로 건다 → 끝나면 하네스가 세션을 깨운다. `.sent` 가 `.seen` 보다 새것이면 바로 끝나서 "기다리기 전에 보낸 것"도 안 놓친다. 상한 110분(백그라운드 2시간 안).
- **깨우기 2 = 채널**(2026-11-09, 사용자 "버튼 누르는 행위가 있는데 왜 대기를 해?"·"대기하면 PC 리소스"·"세션이 대기해야 하는 번거로운 일 안 생기게").
  Claude Code Channels(연구 미리보기, 2.1.319): MCP 서버가 `notifications/claude/channel` 을 보내면 세션이 쉬고 있어도 깨어나고, 바쁘면 줄 선다. 응답 확인(ack)은 없다.
  이 서버 하나가 `/mcp`(HTTP MCP)로 모든 세션을 받는다 — 세션마다 프로세스를 띄우지 않는다(서버 22 MB·CPU 0.1%, board wait 는 세션당 13~22 MB).
  `chan_loop`(1.5초)가 `.sent` 를 보고 넣은 뒤 `.seen` — PC 화면·`board reply`(아티팩트) 둘 다 같은 길이고, 나중에 붙은 세션에도 밀린 것이 간다.
  자리 비움 폰 알림도 30초마다 채널로(채널 세션엔 exit 4 가 없어서). 규칙은 `away_due` 하나 — 자리 비움 한 번에 작업마다 한 번.
  세션 찾기: headersHelper `board channel-headers` 가 부모 프로세스를 거슬러 claude pid 를 보낸다 → `sessions/<pid>.json` 의 sessionId·name.
  헤더에 `${CLAUDE_CODE_SESSION_ID}` 는 안 펼쳐진다(빈 값) — 그래서 pid. 배달 = 문서 `sid:` 가 먼저, 없으면 이름이 하나뿐일 때만(같은 이름 여럿이면 안 보낸다 — 잘못 가느니 안 간다).
  채널이 켜졌는지 = `/proc/<pid>/cmdline` 에 `server:board` — 플래그 없이 뜬 세션도 MCP 로는 붙는데, 거기 넣으면 소리 없이 버려진다.
  `initialize` 에 protocolVersion `2025-06-18` 로 답한다 — `server/discover`(2026-07-28 신식)에 답하면 채널을 안 본다. -32601 이면 옛 방식으로 내려온다.
  채널은 `--dangerously-load-development-channels server:board` 로 띄워야만 켜진다(설정·환경변수 없음), 띄울 때마다 경고 창 한 번, `-p` 에선 꺼진다(화면 없는 세션 불가).
  Orca 는 자기 `claude` 함수가 `command claude` 를 부르니 별칭이 안 먹는다 → PATH 래퍼. `~/.local/bin/claude` 는 자동 업데이트가 갈아 끼워서 못 고친다.
  아티팩트 답(`board reply`)을 담당 세션(문서 `sid` = 부른 세션)이 부르면 읽음까지 한다 — 안 하면 채널(전에는 board wait)이 같은 답으로 한 번 더 깨워 `⇒` 가 두 번 달린다.
  판정은 sid 만(이름은 겹친다 — 남이 읽음 처리하면 담당이 못 깬다). PC 화면에서 보낸 게 아직 안 갔으면 읽음 처리하지 않는다(그것까지 삼킨다).
  쓰지 않은 것: Claude Code 내부 메시지 소켓(`CLAUDE_CODE_MESSAGING_SOCKET`) — 문서에 없는 내부용. 세션 쪽 asyncRewake 훅 — 결국 세션마다 대기가 남는다.
- "내 차례" = 질문의 마지막 말이 세션(`⇒`)이거나 말이 없음. 상태 필드를 따로 두지 않아 사람이 gedit 로 고쳐도 맞다.
- 127.0.0.1 만 연다 — 로그인이 없으니 밖으로 열지 않는다. 폰에서 보기는 비밀번호와 함께 따로(2단계).
- claude.ai 아티팩트는 폐기 — 여러 세션이 읽고 쓰기는 되지만 화면에서 답을 써도 세션을 깨울 방법이 없고(댓글 "Claude 에게 보내기"·지켜보는 세션만), 사내 문서가 밖에 저장된다.
- 알림은 브라우저가 아니라 서버가 띄운다 — 탭을 닫아도 뜨고, 숨은 탭 타이머가 크롬에서 1분 단위로 묶이는 문제도 없다. 키 = 작업#질문#말 수 라서 세션이 되물어도 다시 뜬다. notify-send 0.7.9 는 누르기 동작(-A)이 없어 gi(libnotify)로 띄우고, gi 가 없으면 notify-send 로 글만.
- 경로·포트는 코드에 박지 않는다(`~/.config/board/env`) — 다른 사람 PC 에 그대로 깔기 위해(공용화 대비).

- 옛 txt 문서 묶음(`BOARD_EXTRA`)은 기본 꺼짐(2026-11-03 사용자 "텍스트문서는 없어도 될듯") — [보내기]가 없고, 결정 끝난 항목에 남은 빈 `→ 사용자:` 칸까지 '내 차례'로 세어 무엇을 하라는지 헷갈렸다. 코드는 남김(기본값이 비어 있어 남의 PC 엔 원래 안 뜸).
- 듣는 세션 판별 = `/proc` 명령줄에서 `board wait <작업>` 을 찾는다(2초 캐시) — 세션 쪽에 등록 파일을 두면 이미 떠 있는 옛 코드 wait 가 안 잡혀서.
  Claude Code 는 메모리가 모자라면 쉬는 세션의 백그라운드 셸을 정리한다 → 2026-11-04 밤 4개 세션이 [보내기]를 못 받았다. 화면 경고 + `board orphans`(아무 세션이 SendMessage 로 대신 깨움).
- 밖에서 보기 = 세션마다 아티팩트 한 장(그 세션이 `board push` 로 직접 올림 — 그 세션의 진행 중 작업 전부, 주소는 `~/board/.sessions/<ref>.json`) + 목록 아티팩트 한 장(db `tasks`, 세션이 자기 줄을 씀, PC 별 묶음).
  2026-11-04 사용자 "지금 세션 안통하고 싶거든… 중계세션 자체가 싫다" · "로컬은 그 PC 세션만, 아티팩트는 여러 PC" · "질문과 답을 두 곳에 보내 둘이 똑같게".
  아티팩트 [보내기](comments `sendToClaude`)는 **그 아티팩트를 올린 세션만** 깨운다 — 한 장에 모으면 올린 세션이 결국 중계가 되어서 세션마다 따로(처음엔 작업마다였다가 사용자 "같은 세션끼리만 두면 되잖아"로 세션 단위).
  목록은 web/index.html 의 목록 모드(`board hubpage`)로 PC 화면과 같은 모양 — 왼쪽 PC·세션 접기, 오른쪽 내용(읽기 전용), '답하기'는 세션 아티팩트로.
  폐기한 것: 중계 세션이 전체를 한 장으로 다시 올리기(`board snapshot`/`watch`/`notice`) — 이 PC 에 중계 세션이 늘 떠 있어야 하고(망이 다른 PC 불가), 바뀔 때마다 깨어 토큰을 썼다.
  예외 처리: 세션 꺼짐→[남겨 두기](다시 push 할 때 0번에서 줍기), 바쁨→자동 답글 뒤 처리, 길이 4 KB, 속도 제한, 사진 3/10 MB, 아티팩트 삭제→`--reset`, 회사 내부 자료→`local: yes`.
- 세션 아티팩트·"그 세션의 작업 묶음" 키 = 문서 `sid`(없으면 이름 옆 ref) — ref 는 세션을 다시 띄우면 바뀐다(11-09 일괄 재시작: 이모저모 848664→a6dd3e).
  ref 로 묶으면 새 작업은 새 아티팩트, 옛 작업은 옛 아티팩트로 갈라졌다. `.sessions/<sid>.json` 이 없고 옛 `<ref>.json` 이 있으면 처음 쓸 때 옮겨 같은 주소를 이어 쓴다.
- 자리 비움 = 키보드·마우스 입력이 30분 없음(`BOARD_AWAY_MIN`). 처음엔 화면 잠김 즉시였는데 잠깐 자리 뜰 때도 폰이 울려 30분으로(2026-11-03 사용자). 잠겨도 GNOME 유휴 시간은 계속 쌓인다.
- **사용법(세션이 할 일)이 바뀌면 떠 있는 세션 전부에 SendMessage 로 알린다**(2026-11-03 사용자 "쓰는법이 바뀌어도 다른 세션에 다 전달해야해").
  스킬·CLAUDE.md 규칙만 고치면 이미 떠 있는 세션은 모른다. ListAgents 의 interactive 세션 전부, 무엇이 바뀌었고 무엇을 해야 하는지만 짧게.
  이미 걸어 둔 `board wait` 는 옛 코드로 돈다 — 새 동작이 필요하면 "다시 걸어 달라"를 같이 적는다.

## 트러블슈팅
| 증상 | 원인 | 해결 |
|---|---|---|
| 코드 블록 안 `# 주석` 이 질문을 쪼갬 | 제목 판정이 펜스를 몰랐다 | `fences()` 로 펜스 안은 제목·답으로 안 읽음(고침, test_board.py 가 지킴) |
| 채널 세션인데 [보내기]가 안 들어감 | 세션을 플래그 없이 띄움(`/proc/<pid>/cmdline` 에 `server:board` 없음) | `command -v claude` 가 래퍼인지, PATH 줄 확인 후 다시 띄움 |
| 서버 재시작 뒤 채널 세션 전부 끊김(`/api/channels` 가 `[]`) | 세션이 옛 MCP 세션 id 로 스트림(GET)만 다시 여는데 서버가 404 → Claude Code 는 두 번 해 보고 포기("SSE GET-stream reconnection exhausted", 11-09 install.sh 재시작) | 모르는 id 라도 머리글 `X-Board-Pid` 가 산 프로세스면 받아 준다(고침). 이미 끊긴 세션은 그 세션에 `/mcp reconnect board` |
| `/api/answer` 가 400 `요청 오류: `(빈 메시지) | 질문 머리에 `[ ]` 가 없어 질문을 못 찾음(StopIteration) | `### [ ] Q1. …` |
| 옛 txt 가 진행 중/끝 구분 불가 | txt 엔 끝 표시가 없다 | 빈 `→ 사용자:` 칸·`[ ]`/`[~]` 있거나 이틀 안에 고쳤으면 진행 중 |

## 다음 단계
- 1단계 끝(2026-11-03 사용자 "다끝난거아냐? 잘작동해") — `board-v1` 은 끝난 작업으로 내림. 아래는 사용자가 부탁할 때만.
- 2단계 남은 후보: 폰에서 보기(비밀번호) · 세션 생존 표시.
- 채널이 정식 기능이 되면(연구 미리보기 졸업) 래퍼·경고 창을 정리하고 `board wait` 를 걷어낸다.
- 공용화: 저장소 위치(개인 GitHub / 팀 저장소) 결정 후 README 설치법 다듬기.
