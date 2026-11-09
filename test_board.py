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
print("ok")
