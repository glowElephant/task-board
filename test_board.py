"""board.py 문서 해석·고쳐 넣기 자가 점검 — `python3 test_board.py` 가 조용히 끝나면 통과."""
import json

import board

DOC = """---
title: 시험
session: 세션 [abc123]
status: 진행 중
---
## 요약
한 줄

## 정할 것
### [ ] Q1. 막을까?
A 막는다 · B 그대로

### [ ] Q2. 이미 답한 것
설명
-> 첫 답
⇒ 반영함
   이어지는 줄

### [v] Q3. 끝난 것
-> ㅇㅋ

```bash
# 코드 안 주석은 제목이 아니다
-> 이것도 답이 아니다
```

## 메모
옛 메모
"""


def blocks(text):
    return board.parse(board.split(text)[1])


def items(text):
    return {b["id"]: b for b in blocks(text) if b["type"] == "item"}


it = items(DOC)
assert [*it] == ["Q1", "Q2", "Q3"], it.keys()
assert it["Q1"]["turn"] == "me" and it["Q2"]["turn"] == "me" and it["Q3"]["turn"] == "done"
assert it["Q2"]["entries"] == [{"who": "me", "text": "첫 답"}, {"who": "claude", "text": "반영함\n이어지는 줄"}]
assert "# 코드 안 주석" in it["Q3"]["desc"] and len(it["Q3"]["entries"]) == 1   # 펜스 안은 그대로

# 덧붙이기: 질문 끝·메모 끝에 쌓이고, 세션 말 뒤면 새 말이 된다
d = board.add_answer(DOC, "Q2", "또 하나")
assert [e["who"] for e in items(d)["Q2"]["entries"]] == ["me", "claude", "me"] and "### [v] Q3." in d
memo = [b for b in blocks(board.add_answer(DOC, board.MEMO, "질문\n![](img/a.png)")) if b["type"] == "memo"][0]
assert memo["desc"] == "옛 메모" and memo["entries"] == [{"who": "me", "text": "질문\n![](img/a.png)"}]
assert "## 메모\n-> 새 메모" in board.add_answer("본문만", board.MEMO, "새 메모")
assert board.add_answer(DOC, "Q1", "  ") == DOC

assert board.split(board.set_meta(DOC, "status", "끝"))[0]["status"] == "끝"
import tempfile, pathlib as _p
assert board.is_local({"local": "yes"}) and not board.is_local({})

# 채널 배달 대상: sid 가 먼저, 없으면 이름이 하나뿐일 때만(같은 이름이 여럿이면 안 보낸다)
import time as _t
live = [("m1", 11, {"sessionId": "s1", "name": "가"}), ("m2", 12, {"sessionId": "s2", "name": "나"})]
board._cinfo.update(at=_t.time() + 1e9, v=(live, {"가": 1, "나": 2}))
assert board.chan_targets({"sid": "s2", "session": "가"}) == ["m2"]
assert board.chan_targets({"session": "가 [abc123]"}) == ["m1"]
assert board.chan_targets({"session": "나"}) == []
assert board.chan_targets({"sid": "없는세션", "session": "다"}) == []

# deliver: [보내기]는 한 번만 넣고 읽음 처리
import queue as _q
board.BOARD, board.EXTRA = _p.Path(tempfile.mkdtemp()), []
(board.BOARD / "t").mkdir()
(board.BOARD / "t" / "task.md").write_text("---\ntitle: 시험 작업\nsession: 가\nsid: s1\n---\n## 정할 것\n### [ ] Q1. 할까?\n-> 응\n")
board.send(board.BOARD / "t", ["Q1"])
q = _q.Queue()
board._chans["m1"] = {"pid": 11, "q": q}
board.deliver()
board.deliver()
assert q.qsize() == 1 and "-> 응" in q.get()["params"]["content"] and not board.unread(board.BOARD / "t")
# 받은 시각: 서버 시작 때 처음 보는 옛 문서의 말은 '' (모름), 그 뒤 새로 생긴 말만 문서 수정 시각. 같은 글 두 번도 따로
_sd = _p.Path(tempfile.mkdtemp())
_b = blocks(DOC)
board.stamp(_sd, _b, 0, first=True)
assert [e["at"] for b in _b for e in b.get("entries", ())] == ["", "", ""]
_b = blocks(board.add_answer(board.add_answer(DOC, "Q1", "ㅇㅋ"), "Q3", "ㅇㅋ"))
board.stamp(_sd, _b, _t.mktime((2026, 11, 11, 9, 5, 0, 0, 0, -1)))
_at = {(b["id"], e["text"]): e["at"] for b in _b if b["type"] == "item" for e in b["entries"]}
_q3 = [e["at"] for b in _b if b.get("id") == "Q3" for e in b["entries"]]
assert _q3 == ["", "2026-11-11 09:05"], _q3
assert _at[("Q1", "ㅇㅋ")] == "2026-11-11 09:05" and _at[("Q2", "첫 답")] == ""
_sd2 = _p.Path(tempfile.mkdtemp()); _b = blocks(board.add_answer(DOC, "Q1", "새 작업 말"))
board.stamp(_sd2, _b, _t.mktime((2026, 11, 11, 9, 6, 0, 0, 0, -1)))   # 서버가 떠 있을 때 생긴 문서는 처음부터 시각
assert [e["at"] for b in _b if b.get("id") == "Q1" for e in b["entries"]] == ["2026-11-11 09:06"]
# 밖의 문(터널): 로그인한 기기만, 채널은 로그인해도 403. 이 PC 문은 그대로
import re as _re, argparse as _a
board.LOGINS = _p.Path(tempfile.mkdtemp()) / "logins.json"
def _h(public, cookie="", method="GET", origin=None, host=None):
    h = board.Handler.__new__(board.Handler)
    h.server = type("S", (), {"public": public})()
    h.client_address, h.command, out = ("127.0.0.1", 1), method, []
    h.headers = {"Cookie": cookie, "Host": host or ("board.x" if public else "127.0.0.1:8770"), **({"Origin": origin} if origin else {})}
    h.reply = lambda body, ctype=None, code=200, headers=None: out.append((code, headers or {}))
    return h, out
def _gate(public, path, cookie="", **k):
    h, out = _h(public, cookie, **k)
    return h.gate(path), [c for c, _ in out]
assert _gate(False, "/mcp") == (True, [])
# 이 PC 문: 다른 사이트의 쓰기·다른 이름으로 온 요청(DNS 리바인딩)은 막고, 이 PC 화면·Origin 없는 클라이언트(Claude Code·curl)는 받는다
assert _gate(False, "/api/answer", method="POST", origin="https://evil.example") == (False, [403])
assert _gate(False, "/api/answer", method="POST", origin="null") == (False, [403])
assert _gate(False, "/api/tasks", host="evil.example:8770") == (False, [403])
assert _gate(False, "/api/answer", method="POST", origin="http://localhost:8770", host="localhost:8770") == (True, [])
assert _gate(False, "/mcp", method="POST") == (True, [])
assert _gate(True, "/") == (False, [200])
assert _gate(True, "/api/tasks", "board=nope") == (False, [401])
assert _gate(True, "/mcp") == (False, [403])
# 코드 받기 → 슬랙(가짜) → 코드 → 3달 쿠키 → 들어감. 1분 안 다시 받기·틀린 코드·쓴 코드·로그아웃은 막힘
sent = []
board.slack = lambda text: sent.append(text)
_h(True)[0].login_code(); code = _re.search(r"\d{6}", sent[-1]).group()
_h(True)[0].login_code(); assert len(sent) == 1
h, out = _h(True); h.login(b"code=" + (b"000000" if code != "000000" else b"111111")); assert out[-1][0] == 401
h, out = _h(True); h.login(f"code={code}".encode()); assert out[-1][0] == 303, out
ck = out[-1][1]["Set-Cookie"]; tok = ck.split(";")[0].split("=", 1)[1]
assert "HttpOnly" in ck and "Secure" in ck and "SameSite=Lax" in ck and f"Max-Age={90 * 86400}" in ck
assert tok not in board.LOGINS.read_text() and oct(board.LOGINS.stat().st_mode)[-3:] == "600"
assert _gate(True, "/api/tasks", f"board={tok}") == (True, [])
assert _gate(True, "/mcp", f"board={tok}") == (False, [403])
assert _gate(True, "/api/answer", f"board={tok}", method="POST", origin="https://evil.example") == (False, [403])
assert _gate(True, "/api/answer", f"board={tok}", method="POST", origin="https://board.x") == (True, [])
h, out = _h(True); h.login(f"code={code}".encode()); assert out[-1][0] == 401   # 코드는 한 번만
board._save_logins({}); assert _gate(True, "/api/tasks", f"board={tok}") == (False, [401])   # 모든 기기 로그아웃
# 🔔: 진행 중이면 안 보내고, 끝이 되면 한 번 보내고 지운다
(board.BOARD / "t" / ".bell").touch(); sent.clear(); board.ring_done(); assert sent == []
board.cmd_done(_a.Namespace(task="t")); board.ring_done(); board._rang.clear(); board.ring_done()
assert len(sent) == 1 and "시험 작업" in sent[0] and not (board.BOARD / "t" / ".bell").exists(), sent
# 배너: 서버 시작 때는 기록만, 진행 중 → 끝 으로 바뀔 때 한 번(이미 끝이던 작업은 안 띄움)
board.POPUP = True
(board.BOARD / "u").mkdir(); (board.BOARD / "u" / "task.md").write_text("---\ntitle: 배너 작업\nsession: 버그 [abc123]\nstatus: 진행 중\n---\n## 요약\n지금: 고치는 중\n")
board.watch_done(first=True); assert board._pops.empty()
_t.sleep(0.02); board.cmd_done(_a.Namespace(task="u")); board.watch_done(); board.watch_done()
_pop = board._pops.get_nowait(); assert _pop["title"] == "배너 작업" and _pop["session"] == "버그 [abc123]" and board._pops.empty(), _pop
# 새 작업 맡기기: 고른 세션 sid 로 문서, 할 일은 메모에 내 말로, 대기실 사진은 작업 img/ 로, 🔔, 바로 배달(첫 답 전까지 '새 작업' 안내)
board._cinfo.update(at=_t.time() + 1e9, v=([("m1", 11, {"sessionId": "s1", "name": "가"})], {"가": 1}))
(board.BOARD / ".staging" / "img").mkdir(parents=True); (board.BOARD / ".staging" / "img" / "x.png").write_bytes(b"png")
_tid = board.assign("s1", "로그 봐 줘: 어제 밤", "어제 밤 로그 확인\n![](img/x.png)", bell=True)
_m, _b = board.split((board.BOARD / _tid / "task.md").read_text())
assert _tid == "로그-봐-줘-어제-밤" and _m["sid"] == "s1" and _m["session"] == "가" and _m["from"] == "board", (_tid, _m)
assert (board.BOARD / _tid / "img" / "x.png").is_file() and (board.BOARD / _tid / ".bell").exists() and board.unread(board.BOARD / _tid)
_memo = [b for b in board.parse(_b) if b["type"] == "memo"][0]
assert _memo["entries"] == [{"who": "me", "text": "어제 밤 로그 확인\n![](img/x.png)"}]
assert "새 작업" in board.report_text(_tid, board.BOARD / _tid)
assert board.slug("로그 봐 줘: 어제 밤") == "로그-봐-줘-어제-밤-2"
try:
    board.assign("없는", "제목", "할 일"); raise AssertionError("없는 세션에 맡김")
except KeyError:
    pass
# 멈춤: 진행 중 + 세션 안 일함 + 사용자 차례 질문 없음 → '확인 필요'(mine 1). 일하는 중·기다림·내 차례 질문·끝은 아님
board._live.update(at=_t.time() + 1e9, v={"s1": "idle", "s2": "busy"})
for _n, _sid, _now, _st, _q in [("멈1", "s1", "남은 건 5분 실행(사용자)", "진행 중", "[v]"), ("멈2", "없음", "x", "진행 중", "[v]"),
                                 ("일", "s2", "x", "진행 중", "[v]"), ("기", "s1", "리뷰 기다림", "진행 중", "[v]"),
                                 ("질", "s1", "x", "진행 중", "[ ]"), ("끝", "s1", "x", "끝", "[v]")]:
    (board.BOARD / _n).mkdir()
    (board.BOARD / _n / "task.md").write_text(f"---\ntitle: {_n}\nsid: {_sid}\nstatus: {_st}\n---\n## 요약\n지금: {_now}\n\n## 정할 것\n### {_q} Q1. 할까?\n")
_s = {n: board.summary(n) for n in ["멈1", "멈2", "일", "기", "질", "끝"]}
assert [n for n, s in _s.items() if s["stuck"]] == ["멈1", "멈2"] and _s["멈1"]["mine"] == 1 and _s["질"]["mine"] == 1, _s
# 한도 사용량: 같은 창이면 최댓값(쉬던 세션의 옛 값에 안 덮임), 새 창이면 새 값, 지난 창·없는 창(울트라 5시간)·깨진 파일은 빠짐
board.USAGE = _p.Path(tempfile.mkdtemp()); _T = _t.time()
_w = lambda n, d: (board.USAGE / f"{n}.json").write_text(json.dumps(d))
_win = lambda r, p: {"resets_at": _T + r, "used_percentage": p}
_w("old", {"five_hour": _win(1000, 90), "seven_day": _win(9e4, 10)}); _w("busy", {"five_hour": _win(1030, 40), "seven_day": _win(9e4, 30)})
_w("idle", {"five_hour": _win(1000, 20)}); _w("gone", {"seven_day": _win(-5, 99)})
(board.USAGE / "bad.json").write_text("{"); (board.USAGE / "none.json").write_text('{"five_hour": {}}')
_u = board.usage()
assert _u["five_hour"]["used_percentage"] == 90 and _u["seven_day"]["used_percentage"] == 30, _u
_w("new", {"five_hour": _win(9000, 5)}); assert board.usage()["five_hour"]["used_percentage"] == 5
for _f in board.USAGE.glob("*.json"): _f.unlink()
_w("ultra", {"seven_day": _win(9e4, 24)}); _w("stale5", {"five_hour": _win(-60, 70)})
assert list(board.usage()) == ["seven_day"], board.usage()
print("ok")
