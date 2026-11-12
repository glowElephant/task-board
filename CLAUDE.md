# board (Task Board, 옛 이름 확인판)
여러 Claude Code 세션이 사용자에게 물을 것(판단·결정·승인·알림)을 작업별 문서로 모아 브라우저 탭 하나씩 보여 주는 로컬 도구. 채팅 스크롤에 질문이 묻히는 문제를 없앤다.

## 현재 상태
- 2026-11-03 1단계 완료: 서버·화면·CLI·스킬·전역 CLAUDE.md 규칙, `install.sh` 로 설치(이 PC 설치됨, 사용자 서비스 `board`).
- [보내기] → `board wait` 종료 → 세션 깨움 왕복은 자가 시험으로 확인(2026-11-03).
- 2026-11-03 가로 탭 → 왼쪽 세션별 접히는 목록(작업마다 내 차례 질문 머리 미리보기) — 사용자 "눌러봐야 뭔지 아니까", 작업 17개가 되자 탭이 잘림. 접힘 상태는 localStorage.
- 2026-11-03 데스크톱 알림 추가(사용자 Q3 선택): 내 차례가 새로 생기면 GNOME 알림 — 11-12 뺌.
- 2026-11-04 이름 Task Board(사용자). 화면·알림·문서에 보이는 이름만 — 명령어 `board`·스킬 이름은 그대로(떠 있는 세션이 다시 배울 일 없게).
- 2026-11-04 밖에서 보기: 세션마다 아티팩트 한 장(`board push`) + 목록 아티팩트 한 장 — 11-12 뺌(아래).
- 2026-11-04 플러그인 모양(`.claude-plugin/`, `skills/board/`, `hooks/`) + `board setup` — 다른 사람·다른 PC 는 플러그인 설치 두 줄 + setup.
  저장소 github.com/glowElephant/task-board — 2026-11-04 공개(사용자 결정: 공개하되 회사 안에만 알림). 빈 홈에서 `claude plugin marketplace add glowElephant/task-board` → install 시험 통과.
- 2026-11-09 채널: [보내기]를 세션에 바로 넣는다(`board wait` 불필요). 시험 세션으로 확인 — 쉬는 세션이 깨어나 반영·답(8초), 한 번만, sid 없으면 이름으로, 서버 재시작 3초 뒤 재연결·재배달.
  이 PC: user MCP `board` 등록(`board setup`), 래퍼 `bin/claude`. PATH 줄(~/.bashrc·~/.profile)은 사용자가 넣는다(자동 모드가 셸 시작 파일 수정을 막음).
  채널 없이 뜬 세션은 다시 띄울 때까지 `board wait` 그대로 — 둘이 섞여도 된다. 같은 날 Orca 세션 15개 전부 채널로 다시 띄움.
- 2026-11-09 **모든 작업을 보드로**(사용자 "이제 모든 작업은 테스크보드로", board-everything Q1~Q2=A) — 물을 것만이 아니라 진행·완료 보고·결과 요약까지 작업 문서에, 채팅은 한 줄. 전 세션.
- 2026-11-11 사용자 요청 넷(board-msg-time): 말마다 받은 시각(`.times`) · 목록·작업 머리에 '지금: …' 한 줄(요약에서)과 세션 일하는 중/쉬는 중 ·
  주황 질문 머리를 누르면 그 질문으로 · 규칙 "요약 첫 줄 `지금:`"·"끝나면 그 자리에서 board done". 옛 말 시각은 세션 기록(jsonl)에서 한 번 채움(1,256개, 191개는 모름).

- 2026-11-12 밖(폰)에서 보기를 아티팩트 → 자기 도메인(Cloudflare 터널)으로, 로그인은 슬랙 코드(3달), 🔔 슬랙 끝 알림, 폰 화면(board-domain).
  같은 날 턴마다 뜨던 PC 알림(turn-notify) 대신 작업이 끝날 때 화면 위 배너(세션마다 이모지·색이 다름).

## 구조
```
board.py               서버 + CLI 한 파일(파이썬 표준 라이브러리만). 문서 해석·고쳐 넣기·슬랙 로그인·🔔·setup 도 여기
web/index.html         화면 한 장 — 이 PC 문과 밖의 문(폰)이 같은 코드. 폭 860px 아래면 목록·작업 한 화면씩
popup.py · web/popup.html  작업이 끝날 때 이 PC 화면 위 배너(GTK+WebKit 투명 창) — 서버가 부른다
skills/board/SKILL.md  세션용 절차·형식(플러그인 스킬 · install.sh 는 ~/.claude/skills/board 로 링크)
claude-rule.md         세션이 늘 알아야 할 규칙 — 플러그인은 세션 시작 훅(hooks/session_start.py)이, install.sh 는 전역 CLAUDE.md 에 넣는다
.claude-plugin/        plugin.json · marketplace.json — `claude plugin marketplace add glowElephant/task-board`
install.sh             플러그인 없이 직접 쓰는 PC 용(스킬 링크 · 전역 규칙 · board setup)
bin/claude             대화형 claude 에만 채널 옵션을 붙이는 래퍼(PATH 에서 ~/.local/bin 보다 앞)
test_board.py          해석·고쳐 넣기 자가 점검
```
데이터: `$BOARD_DIR/<작업>/task.md`(+`img/`, `.sent`=보낸 신호, `.seen`=세션이 읽은 표시, `.bell`=🔔 켬(끝 알림을 보내면 지움), `.times`=말마다 받은 시각). 설정 `~/.config/board/env`.
문서 머리 `sid:` = 그 작업을 받을 세션 id(`board new`·`wait` 가 `$CLAUDE_CODE_SESSION_ID` 로 적는다).
로그인한 기기 `~/.config/board/logins.json`(600, sha256(쿠키) → 만료). 비우면 모든 기기 로그아웃.

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
  `chan_loop`(1.5초)가 `.sent` 를 보고 넣은 뒤 `.seen` — 이 PC 화면·밖의 문 둘 다 같은 길이고, 나중에 붙은 세션에도 밀린 것이 간다.
  세션 찾기: headersHelper `board channel-headers` 가 부모 프로세스를 거슬러 claude pid 를 보낸다 → `sessions/<pid>.json` 의 sessionId·name.
  헤더에 `${CLAUDE_CODE_SESSION_ID}` 는 안 펼쳐진다(빈 값) — 그래서 pid. 배달 = 문서 `sid:` 가 먼저, 없으면 이름이 하나뿐일 때만(같은 이름 여럿이면 안 보낸다 — 잘못 가느니 안 간다).
  채널이 켜졌는지 = `/proc/<pid>/cmdline` 에 `server:board` — 플래그 없이 뜬 세션도 MCP 로는 붙는데, 거기 넣으면 소리 없이 버려진다.
  `initialize` 에 protocolVersion `2025-06-18` 로 답한다 — `server/discover`(2026-07-28 신식)에 답하면 채널을 안 본다. -32601 이면 옛 방식으로 내려온다.
  채널은 `--dangerously-load-development-channels server:board` 로 띄워야만 켜진다(설정·환경변수 없음), 띄울 때마다 경고 창 한 번, `-p` 에선 꺼진다(화면 없는 세션 불가).
  Orca 는 자기 `claude` 함수가 `command claude` 를 부르니 별칭이 안 먹는다 → PATH 래퍼. `~/.local/bin/claude` 는 자동 업데이트가 갈아 끼워서 못 고친다.
  쓰지 않은 것: Claude Code 내부 메시지 소켓(`CLAUDE_CODE_MESSAGING_SOCKET`) — 문서에 없는 내부용. 세션 쪽 asyncRewake 훅 — 결국 세션마다 대기가 남는다.
- 받은 시각 = 서버가 1.5초마다 바뀐 문서를 다시 읽어 새 말에 그 문서 수정 시각을 단다(`.times`, 키 = 질문·누구·글·같은 글 몇 번째).
  문서에 시각을 넣지 않은 이유: 세션은 Edit 로 `⇒` 를 쓰니 15개 세션이 시각을 직접 적어야 한다 — 서버가 보면 세션은 바뀔 게 없다.
  서버 시작 때 `.times` 없는 문서의 말은 '' (모름) — 그 순간 시각으로 찍으면 옛 말이 전부 지금 받은 것처럼 보인다. 글을 고치면 새 말로 친다.
- '지금' 한 줄 = 요약의 `지금: …` 줄(없으면 요약 첫 줄) — 새 칸(머리 `doing:`)을 안 만든 이유: 세션은 요약을 이미 계속 고쳐 쓴다, 칸이 늘면 잊는다.
  세션 일하는 중/쉬는 중 = Claude Code 가 `sessions/<pid>.json` 에 적는 `status`(idle 이 아니면 일하는 중), 문서 `sid` 로 찾는다. 세션 단위라 이 작업 일이 아닐 수 있다.
- "내 차례" = 질문의 마지막 말이 세션(`⇒`)이거나 말이 없음. 상태 필드를 따로 두지 않아 사람이 gedit 로 고쳐도 맞다.
- **밖(폰)에서 보기 = 자기 도메인 → Cloudflare 터널 → 밖의 문**(2026-11-12 board-domain, 사용자 "도메인에 띄워서 외부에서도"·"아티팩트연동은빼고… 너무 복잡"·
  "암호는 간단히 말고 슬랙으로 코드 받아서"·"한번 로그인하면 기억하고 최대 3달").
  - 문이 둘이다. 이 PC 문(`BOARD_PORT`, 127.0.0.1 만) = 그대로 무로그인. 밖의 문(`BOARD_PUBLIC_PORT`, 127.0.0.1 만) = 터널만 붙고 모든 요청이 로그인 필요.
    포트로 나눈 이유: 터널에서 온 요청도 출발지가 127.0.0.1 이라 IP 로는 이 PC 와 구분이 안 된다. 밖의 문은 SLACK_BOT_TOKEN·BOARD_SLACK_EMAIL 이 있어야 연다.
  - 로그인 = 슬랙 DM 6자리 코드(운영보드와 같은 길 — 회사 메일 → `users.lookupByEmail` → `chat.postMessage`, 운영보드 봇 토큰을 같이 씀).
    코드 5분·한 번·5번 틀리면 버림, 코드 받기 1분 1번·1시간 10번(아무나 눌러 슬랙을 울리지 못하게), IP(`Cf-Connecting-Ip`)마다 5번 틀리면 10분.
    성공하면 쿠키 `board`(32바이트 난수, 90일 고정 — 미루지 않음, HttpOnly·Secure·SameSite=Lax). Lax 인 이유: 슬랙 알림 링크를 눌러 열 때 Strict 면 쿠키가 안 실려 매번 로그인 화면.
    POST 는 Origin 이 Host·BOARD_PUBLIC_URL 과 다르면 403(한 겹 더).
  - 밖의 문에선 `/mcp`·`/api/channels` 403(로그인해도) — [보내기]가 세션에 '사용자 말'로 들어가고 세션은 이 PC 에서 명령을 실행한다. 그래서 암호 대신 슬랙 코드.
  - 밖의 문에선 `local: yes` 작업을 없는 작업처럼(회사 결재 자료 — 원래 아티팩트로 안 보내던 것). 이 PC 화면엔 보인다.
  - 폐기: 6자리 고정 암호 + 0.0.0.0(11-11) — 평문 HTTP 였고 사내망 다른 IP 로도 열렸다. 터널이 https 를 대신하고 밖의 문은 127.0.0.1 만 연다.
- **알림은 둘**(11-12 사용자 "알림도 빼자… 보지도 않아… 알림버튼 눌러서 그 테스크 끝나면" → 같은 날 "세션이 끝날 때마다 알림이 상단에… 너무 자주… 이 PC 에서 task 가 끝나면 상단에 · 엄청 화려하게 · 세션마다 달라도 · 특징적이게").
  - 🔔 = 작업 화면에서 켜면 `.bell`, `ring_done` 이 1.5초마다 보다가 문서 status 가 끝이면 슬랙 DM 한 번 보내고 `.bell` 을 지운다(실패하면 1분 뒤 다시). 폰용.
  - 이 PC 배너 = 모든 작업이 진행 중 → 끝 으로 바뀌는 순간(`watch_done`, 문서 mtime 캐시) `popup.py` 를 띄운다(줄을 세워 하나씩). 서버 시작 때는 기록만.
    GNOME 알림이 아니라 직접 그린 창인 이유: 알림은 모양을 못 바꾼다. GTK3 + WebKit2 4.0 투명 창(X11·합성 관리자), 포커스를 안 뺏고(type hint NOTIFICATION, accept_focus False),
    누르면 그 작업 화면, 9초 뒤 닫힘(마우스 올리면 멈춤). 모양은 `web/popup.html` — 세션 이름 낱말로 이모지·색 셋·머리말(버그→🐞 "잡았다!", ci/cd→🚀 …), 모르는 이름은 해시 색.
    시험은 화면에 안 띄우고 `python3 popup.py '<JSON>' --shot out.png 1500`(OffscreenWindow 스냅숏 — 헤드리스 크롬은 WebKit 과 달라 쓰지 않는다).
    세션이 자기 배너를 꾸민다(11-12 사용자 "각 세션한테 배너 디자인하라그래 자기 색깔에 맞게"): `$BOARD_DIR/.banners/<세션 이름>.json`
    {emoji, colors[3], heads[], confetti[], css} 중 있는 칸만 기본을 덮는다. 세션마다 파일 하나 — popup.html 을 14곳이 고치면 부딪친다. 키는 이름(sid·ref 는 다시 띄우면 바뀐다).
    `board banner` = 파일 만들기·경로, `--shot` = 화면에 안 띄우고 PNG 두 장(터질 때 1.3초·가라앉은 뒤 3.2초). 20 KB 넘거나 깨진 JSON 은 무시(기본 모양).
  - 같은 날 끈 것: 전역 훅 `~/.claude/hooks/turn-notify.sh`(1분 넘은 턴마다 "<세션> — 작업 완료" GNOME 알림) — settings.json 에서 빼고 파일은 no-op(떠 있던 세션이 아직 부름).
  뺀 것(11-12): GNOME 알림(내 차례마다), 자리 비움 감지(GNOME 유휴 30분) + 폰 알림(채널 `[자리 비움]`·`board wait` exit 4 → 세션이 PushNotification).
- 경로·포트는 코드에 박지 않는다(`~/.config/board/env`) — 다른 사람 PC 에 그대로 깔기 위해(공용화 대비).

- 옛 txt 문서 묶음(`BOARD_EXTRA`)은 기본 꺼짐(2026-11-03 사용자 "텍스트문서는 없어도 될듯") — [보내기]가 없고, 결정 끝난 항목에 남은 빈 `→ 사용자:` 칸까지 '내 차례'로 세어 무엇을 하라는지 헷갈렸다. 코드는 남김(기본값이 비어 있어 남의 PC 엔 원래 안 뜸).
- 듣는 세션 판별 = `/proc` 명령줄에서 `board wait <작업>` 을 찾는다(2초 캐시) — 세션 쪽에 등록 파일을 두면 이미 떠 있는 옛 코드 wait 가 안 잡혀서.
  Claude Code 는 메모리가 모자라면 쉬는 세션의 백그라운드 셸을 정리한다 → 2026-11-04 밤 4개 세션이 [보내기]를 못 받았다. 화면 경고 + `board orphans`(아무 세션이 SendMessage 로 대신 깨움).
- 폐기(11-12): claude.ai 아티팩트로 밖에서 보기(11-04~11-11) — 세션 아티팩트 한 장(`board push`)·목록 아티팩트(db `tasks`)·댓글로 답 받기(`board reply`)·[최신으로].
  사용자 "너무 복잡한것같아서". 세션마다 올리는 도구 호출 4번, 찍어 둔 때 기준이라 늘 낡음, 사내 문서가 claude.ai 에 저장됐다. 만든 아티팩트 2장은 지우지 않고 둠.
- **사용법(세션이 할 일)이 바뀌면 떠 있는 세션 전부에 SendMessage 로 알린다**(2026-11-03 사용자 "쓰는법이 바뀌어도 다른 세션에 다 전달해야해").
  스킬·CLAUDE.md 규칙만 고치면 이미 떠 있는 세션은 모른다. ListAgents 의 interactive 세션 전부, 무엇이 바뀌었고 무엇을 해야 하는지만 짧게.
  이미 걸어 둔 `board wait` 는 옛 코드로 돈다 — 새 동작이 필요하면 "다시 걸어 달라"를 같이 적는다.

## 트러블슈팅
| 증상 | 원인 | 해결 |
|---|---|---|
| 코드 블록 안 `# 주석` 이 질문을 쪼갬 | 제목 판정이 펜스를 몰랐다 | `fences()` 로 펜스 안은 제목·답으로 안 읽음(고침, test_board.py 가 지킴) |
| 채널 세션인데 [보내기]가 안 들어감 | 세션을 플래그 없이 띄움(`/proc/<pid>/cmdline` 에 `server:board` 없음) | `command -v claude` 가 래퍼인지, PATH 줄 확인 후 다시 띄움 |
| "11-02~08"·"Q1~Q4" 가 취소선으로 그어짐 | marked 는 물결표 하나(`~글~`)도 취소선으로 읽는다 | `web/index.html` 의 `del` 토크나이저 덮어쓰기 — `~~두 개~~` 만 취소선(11-09 고침) |
| 서버 재시작 뒤 채널 세션 전부 끊김(`/api/channels` 가 `[]`) | 세션이 옛 MCP 세션 id 로 스트림(GET)만 다시 여는데 서버가 404 → Claude Code 는 두 번 해 보고 포기("SSE GET-stream reconnection exhausted", 11-09 install.sh 재시작) | 모르는 id 라도 머리글 `X-Board-Pid` 가 산 프로세스면 받아 준다(고침). 이미 끊긴 세션은 그 세션에 `/mcp reconnect board` |
| `/api/answer` 가 400 `요청 오류: `(빈 메시지) | 질문 머리에 `[ ]` 가 없어 질문을 못 찾음(StopIteration) | `### [ ] Q1. …` |
| 옛 txt 가 진행 중/끝 구분 불가 | txt 엔 끝 표시가 없다 | 빈 `→ 사용자:` 칸·`[ ]`/`[~]` 있거나 이틀 안에 고쳤으면 진행 중 |

## 다음 단계
- 1단계 끝(2026-11-03 사용자 "다끝난거아냐? 잘작동해") — `board-v1` 은 끝난 작업으로 내림. 아래는 사용자가 부탁할 때만.
- 2단계 남은 후보: 세션 생존 표시. (폰에서 보기는 11-12 도메인으로 됨)
- 채널이 정식 기능이 되면(연구 미리보기 졸업) 래퍼·경고 창을 정리하고 `board wait` 를 걷어낸다.
- 공용화: 저장소 위치(개인 GitHub / 팀 저장소) 결정 후 README 설치법 다듬기.
