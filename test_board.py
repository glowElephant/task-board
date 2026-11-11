"""board.py 문서 해석·고쳐 넣기 자가 점검 — `python3 test_board.py` 가 조용히 끝나면 통과."""
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
assert not board.local_only.__doc__ is None
import tempfile, pathlib as _p
_f = _p.Path(tempfile.mkdtemp()) / "task.md"
_f.write_text("---\ntitle: x\nlocal: yes\n---\n본문\n"); assert board.local_only(_f)
_f.write_text("---\ntitle: x\n---\n본문\n"); assert not board.local_only(_f)

# 채널 배달 대상: sid 가 먼저, 없으면 이름이 하나뿐일 때만(같은 이름이 여럿이면 안 보낸다)
import time as _t
live = [("m1", 11, {"sessionId": "s1", "name": "가"}), ("m2", 12, {"sessionId": "s2", "name": "나"})]
board._cinfo.update(at=_t.time() + 1e9, v=(live, {"가": 1, "나": 2}))
assert board.chan_targets({"sid": "s2", "session": "가"}) == ["m2"]
assert board.chan_targets({"session": "가 [abc123]"}) == ["m1"]
assert board.chan_targets({"session": "나"}) == []
assert board.chan_targets({"sid": "없는세션", "session": "다"}) == []

# deliver: [보내기]는 한 번만 넣고 읽음 처리, 자리 비움 폰 알림은 자리 비움 한 번에 작업마다 한 번
import queue as _q
board.BOARD, board.EXTRA, board.HUB = _p.Path(tempfile.mkdtemp()), [], ""
(board.BOARD / "t").mkdir()
(board.BOARD / "t" / "task.md").write_text("---\ntitle: 시험 작업\nsession: 가\nsid: s1\n---\n## 정할 것\n### [ ] Q1. 할까?\n-> 응\n")
board.send(board.BOARD / "t", ["Q1"])
q = _q.Queue()
board._chans["m1"] = {"pid": 11, "q": q}
board.deliver()
board.deliver()
assert q.qsize() == 1 and "-> 응" in q.get()["params"]["content"] and not board.unread(board.BOARD / "t")
(board.BOARD / "t" / "task.md").write_text("---\ntitle: 시험 작업\nsession: 가\nsid: s1\n---\n## 정할 것\n### [ ] Q1. 할까?\n-> 응\n⇒ 되물음\n")
away = {"state": "away", "away_since": 100}
board.deliver(away)
board.deliver(away)
assert q.qsize() == 1 and "내 차례 1건" in q.get()["params"]["content"]

# reply: 담당 세션이 부르면 읽음까지(같은 답으로 또 안 깨움), 남이 부르거나 PC 에서 보낸 게 밀려 있으면 그대로 보낸다
import argparse as _a, os as _os
_ans = board.BOARD / "ans.txt"; _ans.write_text("아티팩트에서 온 답")
_reply = lambda: board.cmd_reply(_a.Namespace(task="t", item="Q1", file=str(_ans), send=True))
_os.environ["CLAUDE_CODE_SESSION_ID"] = "s1"; _reply(); assert not board.unread(board.BOARD / "t")
_t.sleep(0.02); board.send(board.BOARD / "t", ["Q1"]); _reply(); assert board.unread(board.BOARD / "t")   # 밀린 [보내기]는 삼키지 않는다(파일 시각 단위 때문에 잠깐 띄움)
_t.sleep(0.02); (board.BOARD / "t" / ".seen").write_text("x"); _t.sleep(0.02)
_os.environ["CLAUDE_CODE_SESSION_ID"] = "남"; _reply(); assert board.unread(board.BOARD / "t")
# 세션 아티팩트 묶음: 다시 띄워 ref 가 바뀌어도 sid 가 같으면 한 장, 옛 ref 기록(주소)을 이어 쓴다
for n, ref in (("a1", "aaaaaa"), ("a2", "bbbbbb")):
    (board.BOARD / n).mkdir()
    (board.BOARD / n / "task.md").write_text(f"---\ntitle: {n}\nsession: 나 [{ref}]\nsid: S9\n---\n## 정할 것\n")
assert board.sess_key("a1") == board.sess_key("a2") == "S9" and set(board.session_tasks("S9")) == {"a1", "a2"}
(board.BOARD / ".sessions").mkdir(exist_ok=True)
(board.BOARD / ".sessions" / "aaaaaa.json").write_text('{"url": "https://x/old"}')
assert board.sess_state("S9", old="aaaaaa")["url"] == "https://x/old" and (board.BOARD / ".sessions" / "S9.json").is_file()

# push: 처음 생긴 작업은 올리고, 이미 있으면 건너뛴다(--now 일 때만 — 11-10 B). --now 는 같은 세션의 바뀐 작업 줄도 같이
import io as _io, contextlib as _cl
board.HUB = "https://hub"; _os.environ["CLAUDE_CODE_SESSION_ID"] = "S9"   # push 가 문서에 이 세션 sid 를 적는다
_push = lambda t, now=False: board.cmd_push(_a.Namespace(task=t, now=now, out=None))
def _out(fn):
    b = _io.StringIO()
    with _cl.redirect_stdout(b):
        fn()
    return b.getvalue()
o = _out(lambda: _push("a1"))
assert "1) Artifact publish" in o and "ArtifactData batch" in o and "board pushed a1 a2 --version" in o, o
_out(lambda: board.cmd_pushed(_a.Namespace(task=["a1", "a2"], url=None, version=[3, 4], reset=False)))
assert board.remote(board.BOARD / "a2")["version"] == 4 and board.changed("a1") is False
assert "건너뜀" in _out(lambda: _push("a1"))
_t.sleep(0.02); (board.BOARD / "a2" / "task.md").write_text("---\ntitle: a2\nsession: 나 [bbbbbb]\nsid: S9\n---\n## 정할 것\n### [ ] Q1. 새 질문\n")
o = _out(lambda: _push("a1", now=True))
assert '"if_version": 4' in o and "board pushed a1 a2" in o, o
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
print("ok")
