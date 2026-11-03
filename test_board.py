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

# 새 답은 질문 끝에 붙고, 마지막 말이 내 것이면 그 자리에서 바뀐다
d = board.set_answer(DOC, "Q1", "A 로\n\n단 비상정지는 예외")
assert items(d)["Q1"]["entries"] == [{"who": "me", "text": "A 로\n\n단 비상정지는 예외"}]
assert items(d)["Q1"]["turn"] == "claude"
d = board.set_answer(d, "Q1", "B 로")
assert items(d)["Q1"]["entries"] == [{"who": "me", "text": "B 로"}]
assert "### [ ] Q2." in d and d.count("-> B 로") == 1
# 세션 말 뒤에 답하면 대화가 이어진다
d = board.set_answer(d, "Q2", "다시")
assert [e["who"] for e in items(d)["Q2"]["entries"]] == ["me", "claude", "me"]
# 빈 답은 내 마지막 답을 지운다
assert items(board.set_answer(d, "Q1", ""))["Q1"]["entries"] == []

memo = [b for b in blocks(board.set_memo(DOC, "새 메모\n![](img/a.png)")) if b["type"] == "memo"]
assert memo[0]["text"].strip() == "새 메모\n![](img/a.png)"
assert "## 메모\n새 메모" in board.set_memo("본문만", "새 메모")

assert board.split(board.set_meta(DOC, "status", "끝"))[0]["status"] == "끝"
print("ok")
