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
print("ok")
