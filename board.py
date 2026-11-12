#!/usr/bin/env python3
"""board — Task Board(옛 이름 확인판). 여러 Claude 세션이 사용자에게 물을 것을 작업별 문서(탭 하나)로 모은다.

파일이 원본이다: $BOARD_DIR/<작업>/task.md (+ img/). 서버는 그 파일을 보여 주고 사용자의 답·메모·이미지를
그 자리에 고쳐 넣을 뿐 상태를 따로 갖지 않는다. 사용자가 [보내기]를 누르면 <작업>/.sent 가 바뀌고,
서버가 그 작업 세션에 채널(이 서버의 /mcp)로 바로 넣는다. 채널 없이 띄운 세션은 `board wait` 로 기다린다.

  board serve                               화면 서버 (127.0.0.1:BOARD_PORT) + 채널 MCP(/mcp)
                                            + 슬랙을 설정하면 밖에서 보는 문(127.0.0.1:BOARD_PUBLIC_PORT — Cloudflare 터널이 붙는다)
  board new <작업> --title T --session S    작업 문서 만들기 — 마지막 줄이 board wait 를 걸지 말지 알려 준다
  board wait <작업>... [--max 분]           (채널 없는 세션만) [보내기]까지 대기 — Bash run_in_background 로 건다
                                            exit 0 = 보냄(읽고 반영) · 3 = 시간 초과(다시 건다) · 5 = 이 세션은 채널이 있어 안 기다림
  board done <작업>                         끝으로 표시 (목록 '끝난 작업' 으로) — 🔔 를 켠 작업이면 슬랙 DM 이 간다
  board list                                작업 목록
  board url [<작업>]                        화면 주소
  board banner [--shot]                     이 세션의 '끝' 배너 꾸미기 — 설정 파일 경로·틀, --shot 이면 화면에 안 띄우고 PNG 로

설정: 환경변수 > ~/.config/board/env (KEY=VALUE). BOARD_DIR(기본 ~/board) · BOARD_PORT(기본 8770) ·
BOARD_EXTRA(옛 txt 문서 폴더, os.pathsep 로 여럿 — 탭으로 보여 주기만 한다) ·
SLACK_BOT_TOKEN + BOARD_SLACK_EMAIL 또는 BOARD_SLACK_USER(밖에서 볼 때 로그인 코드와 🔔 끝 알림을 그 사람 슬랙 DM 으로 — 둘 다 있어야 밖의 문이 열린다) ·
BOARD_PUBLIC_PORT(기본 BOARD_PORT+1) · BOARD_PUBLIC_URL(슬랙 알림 링크, 예 https://board.example.com) ·
BOARD_POPUP(0 이면 작업이 끝날 때 이 PC 화면 위 배너(popup.py)를 안 띄운다).
"""
import argparse
import collections
import datetime
import hashlib
import hmac
import http.cookies
import http.server
import json
import mimetypes
import os
import pathlib
import queue
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request

HOME = pathlib.Path.home()


def _config():
    cfg = pathlib.Path(os.environ.get("XDG_CONFIG_HOME") or HOME / ".config") / "board" / "env"
    vals = {}
    if cfg.is_file():
        for ln in cfg.read_text(encoding="utf-8").splitlines():
            k, sep, v = ln.partition("=")
            if sep and not k.strip().startswith("#"):
                vals[k.strip()] = v.strip().strip("'\"")   # 다른 .env 에서 옮겨 온 줄은 따옴표가 있다
    vals.update({k: v for k, v in os.environ.items() if k.startswith("BOARD_")})
    return vals


CFG = _config()
BOARD = pathlib.Path(CFG.get("BOARD_DIR") or HOME / "board").expanduser()
PORT = int(CFG.get("BOARD_PORT") or 8770)
EXTRA = [pathlib.Path(p).expanduser() for p in (CFG.get("BOARD_EXTRA") or "").split(os.pathsep) if p]
WEB = pathlib.Path(__file__).resolve().parent / "web"
SLACK_TOKEN = CFG.get("SLACK_BOT_TOKEN", "")   # 운영보드 슬랙 봇 토큰을 같이 쓴다(users:read.email · chat:write)
SLACK_EMAIL = CFG.get("BOARD_SLACK_EMAIL", "")  # 코드·알림을 받을 사람 — 회사 메일로 슬랙 계정을 찾는다(users:read.email)
SLACK_USER = CFG.get("BOARD_SLACK_USER", "")    # 또는 슬랙 멤버 ID(U…) — 봇에 메일 찾기 권한이 없을 때
PUBLIC_PORT = int(CFG.get("BOARD_PUBLIC_PORT") or PORT + 1)
PUBLIC_URL = CFG.get("BOARD_PUBLIC_URL", "").rstrip("/")
LOGINS = pathlib.Path(os.environ.get("XDG_CONFIG_HOME") or HOME / ".config") / "board" / "logins.json"
USAGE = pathlib.Path(os.environ.get("XDG_CACHE_HOME") or HOME / ".cache") / "board" / "usage"   # 상태줄이 세션마다 남기는 한도 사용량
POPUP = (CFG.get("BOARD_POPUP") or "1") != "0" and bool(os.environ.get("DISPLAY"))
KEEP = 90 * 86400                              # 한 번 로그인하면 3달(11-12 사용자 "최대 3달동안")
_auth = {"code": "", "exp": 0.0, "tries": 0, "sent": []}   # 지금 코드(sha256)·만료·틀린 횟수·보낸 시각
_auth_lock = threading.Lock()
_fails = {}                                    # 밖의 IP → (틀린 횟수, 막힌 끝 시각) — 5번 틀리면 10분
_LOOK = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Task Board</title><style>*{box-sizing:border-box}body{font:16px system-ui,sans-serif;background:#f4f5f7;color:#222;display:grid;place-items:center;min-height:100vh;margin:0}
main{background:#fff;padding:28px;border-radius:10px;box-shadow:0 2px 12px #0002;display:grid;gap:12px;width:min(320px,88vw)}
form{display:grid;gap:12px}input,button{font:inherit;padding:11px;border-radius:6px;border:1px solid #ccc}
input{font-size:22px;letter-spacing:.3em;text-align:center;width:100%;min-width:0}button{background:#d9480f;color:#fff;border:0;font-weight:600}
button.link{background:none;color:#1d5fd1;padding:0;font-weight:400}span{color:#555;font-size:14px}p{margin:0;color:#c33;font-size:14px}
@media (prefers-color-scheme:dark){body{background:#13161b;color:#e5e8ee}main{background:#1b1f26}span{color:#98a2b3}input{background:#13161b;color:#e5e8ee;border-color:#2d333d}}</style>
<main><b>Task Board</b>"""
LOGIN = _LOOK + """<span>슬랙 DM 으로 6자리 코드를 보냅니다.</span><p><!--e--></p>
<form method="post" action="/login/code"><button>슬랙으로 코드 받기</button></form></main>"""
CODE = _LOOK + """<span>슬랙 DM 으로 온 6자리 코드 — 5분 안에 넣어 주세요.</span>
<form method="post" action="/login"><input name="code" size="6" inputmode="numeric" autocomplete="one-time-code" maxlength="6" autofocus>
<p><!--e--></p><button>열기</button></form><form method="post" action="/login/code"><button class="link">코드 다시 받기</button></form></main>"""

FM = re.compile(r"\A---\n(.*?)\n---\n?", re.S)
ITEM = re.compile(r"^###\s*\[([ ~vV])\]\s*([^\s.]+)\.?\s*(.*)$")
HEAD = re.compile(r"^#{1,3}\s")
DONE = {"끝", "done"}
MEMO = "메모"


def now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


# ── 문서 해석 ────────────────────────────────────────────────────────────────

def fences(lines):
    """줄마다 코드 펜스 안인지 — 펜스 안의 `# 주석`·`->` 를 제목·답으로 읽지 않으려고."""
    inside, out = False, []
    for ln in lines:
        if ln.lstrip().startswith("```"):
            out.append(True)
            inside = not inside
        else:
            out.append(inside)
    return out


def split(text):
    m = FM.match(text)
    meta = {}
    if m:
        for ln in m.group(1).splitlines():
            k, sep, v = ln.partition(":")
            if sep:
                meta[k.strip()] = v.strip()
    return meta, (text[m.end():] if m else text)


def scan(lines):
    """질문 하나의 줄 → (설명 줄, 대화). `->` 는 사용자, `⇒` 는 세션. 붙어 있는 다음 줄은 같은 말."""
    desc, ents = [], []
    for i, (ln, fenced) in enumerate(zip(lines, fences(lines))):
        s = ln.strip()
        who = None if fenced else "me" if s.startswith("->") else "claude" if s.startswith("⇒") else None
        last = ents[-1] if ents else None
        glued = last is not None and last["b"] == i
        if who:
            t = s[2:] if who == "me" else s[1:]
            t = t[1:] if t[:1] == " " else t
            if glued and last["who"] == who:
                last["text"] += "\n" + t
                last["b"] = i + 1
            else:
                ents.append({"who": who, "text": t, "a": i, "b": i + 1})
        elif glued and s:
            last["text"] += "\n" + s
            last["b"] = i + 1
        else:
            desc.append(ln)
    return desc, ents


NOW = re.compile(r"^[-*\s]*(?:\*\*)?지금(?:\*\*)?\s*[:：]\s*(.+)$", re.M)


def brief(body):
    """`## 요약` 본문."""
    m = re.search(r"^##\s*요약[ \t]*\n(.*?)(?=^#{1,3}\s|\Z)", body, re.M | re.S)
    return m.group(1).strip() if m else ""


def doing(br):
    """지금 하는 일 한 줄 — 요약의 `지금: …` 줄, 없으면 요약 첫 줄(11-11 사용자 "뭐가 진행중인지 알기가 힘들거든")."""
    m = NOW.search(br)
    line = m.group(1) if m else next((l for l in br.split("\n") if l.strip()), "")
    return re.sub(r"\*\*|`", "", line).strip(" -*")[:140]


_live = {"at": 0.0, "v": {}}


def live():
    """sid → 'busy'·'idle' — Claude Code 가 sessions/<pid>.json 에 적는 status(살아 있는 pid 만). 3초 캐시.
    세션 단위다 — 일하는 중이어도 이 작업이 아닐 수 있다."""
    if time.time() - _live["at"] > 3:
        v = {}
        for f in pathlib.Path(os.environ.get("CLAUDE_CONFIG_DIR") or HOME / ".claude").glob("sessions/*.json"):
            try:
                st = json.loads(f.read_text(encoding="utf-8"))
                if pathlib.Path(f"/proc/{st['pid']}").exists():
                    v[st["sessionId"]] = "idle" if st.get("status") == "idle" else "busy"
            except (OSError, ValueError, KeyError, TypeError):
                pass
        _live.update(at=time.time(), v=v)
    return _live["v"]


def turn(status, ents):
    if status == "v":
        return "done"
    return "claude" if ents and ents[-1]["who"] == "me" else "me"


def parse(body):
    """본문 → 블록 목록: md / item(### [ ] Q1. 제목 ~ 다음 제목) / memo(## 메모 아래)."""
    lines = body.split("\n")
    out, cur = [], None
    for ln, fenced in zip(lines, fences(lines)):
        m = None if fenced else ITEM.match(ln)
        if m:
            cur = {"type": "item", "status": m.group(1).lower(), "id": m.group(2), "title": m.group(3), "lines": []}
            out.append(cur)
            continue
        if not fenced and HEAD.match(ln):
            cur = None
            if ln.lstrip("#").strip() == MEMO:
                out.append({"type": "md", "lines": [ln]})
                cur = {"type": "memo", "lines": []}
                out.append(cur)
                continue
        if cur is None:
            if not out or out[-1]["type"] != "md":
                out.append({"type": "md", "lines": []})
            out[-1]["lines"].append(ln)
        else:
            cur["lines"].append(ln)
    for b in out:
        lines = b.pop("lines")
        if b["type"] == "item":
            desc, ents = scan(lines)
            b["desc"] = "\n".join(desc).strip()
            b["entries"] = [{"who": e["who"], "text": e["text"]} for e in ents]
            b["turn"] = turn(b["status"], ents)
        elif b["type"] == "memo":
            desc, ents = scan(lines)
            b["desc"] = "\n".join(desc).strip()
            b["entries"] = [{"who": e["who"], "text": e["text"]} for e in ents]
        else:
            b["text"] = "\n".join(lines).strip("\n")
    return out


def _item_range(lines, item_id):
    fz = fences(lines)
    start = next(i for i, l in enumerate(lines) if not fz[i] and (m := ITEM.match(l)) and m.group(2) == item_id)
    end = next((i for i in range(start + 1, len(lines)) if not fz[i] and HEAD.match(lines[i])), len(lines))
    return start, end


def add_answer(text, item_id, answer):
    """사용자 말을 질문(item_id) 또는 ## 메모(MEMO) 끝에 덧붙인다 — 화면은 보낸 칸을 비우고 대화에 쌓는다."""
    if not answer.strip():
        return text
    new = [("-> " + l) if l else "->" for l in answer.rstrip().split("\n")]
    lines = text.split("\n")
    fz = fences(lines)
    if item_id == MEMO:
        start = next((i for i, l in enumerate(lines) if not fz[i] and HEAD.match(l) and l.lstrip("#").strip() == MEMO), None)
        if start is None:
            while lines and not lines[-1].strip():
                lines.pop()
            return "\n".join(lines + ["", "## " + MEMO] + new + [""])
        end = next((j for j in range(start + 1, len(lines)) if not fz[j] and HEAD.match(lines[j])), len(lines))
    else:
        start, end = _item_range(lines, item_id)
    k = end
    while k > start + 1 and not lines[k - 1].strip():
        k -= 1
    lines[k:k] = new
    return "\n".join(lines)


def set_meta(text, key, value):
    m = FM.match(text)
    if not m:
        return f"---\n{key}: {value}\n---\n" + text
    rows = [l for l in m.group(1).splitlines() if l.partition(":")[0].strip() != key] + [f"{key}: {value}"]
    return "---\n" + "\n".join(rows) + "\n---\n" + text[m.end():]


# ── 파일 ─────────────────────────────────────────────────────────────────────

def write(path, text):
    tmp = path.with_name("." + path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def resolve(tid):
    """작업 id → (문서 파일, 작업 폴더). 옛 txt 는 'txt:<파일명>', 폴더 없음."""
    if tid.startswith("txt:"):
        name = tid[4:]
        if name and "/" not in name and not name.startswith("."):
            for d in EXTRA:
                f = d / name
                if f.suffix == ".txt" and f.is_file():
                    return f, None
        raise KeyError(tid)
    if not tid or "/" in tid or tid.startswith("."):
        raise KeyError(tid)
    d = BOARD / tid
    if not (d / "task.md").is_file():
        raise KeyError(tid)
    return d / "task.md", d


_heard = {"at": 0.0, "ids": set()}


def listeners():
    """지금 `board wait` 가 듣고 있는 작업 — /proc 명령줄에서 읽는다(세션의 옛 코드 wait 도 잡힌다). 2초 캐시.
    Claude Code 는 메모리가 모자라면 쉬는 세션의 백그라운드 셸을 정리한다 — 그러면 [보내기]가 아무도 못 깨운다(11-04 실측 4개 세션)."""
    if time.time() - _heard["at"] < 2:
        return _heard["ids"]
    ids = set()
    for c in pathlib.Path("/proc").glob("[0-9]*/cmdline"):
        try:
            args = [x.decode(errors="replace") for x in c.read_bytes().split(b"\0") if x]
        except OSError:
            continue
        if "wait" not in args or not any(pathlib.Path(x).name in ("board", "board.py") for x in args[:args.index("wait")]):
            continue
        rest, i = args[args.index("wait") + 1:], 0
        while i < len(rest):
            if rest[i].startswith("--"):
                i += 1 if "=" in rest[i] else 2   # --max 110 / --max=110
                continue
            ids.add(rest[i])
            i += 1
    _heard.update(at=time.time(), ids=ids)
    return ids


def unread(d):
    sent, seen = d / ".sent", d / ".seen"
    return sent.exists() and (not seen.exists() or sent.stat().st_mtime_ns > seen.stat().st_mtime_ns)


def summary(tid):
    f, d = resolve(tid)
    text = f.read_text(encoding="utf-8", errors="replace")
    if d is None:
        mine = len(re.findall(r"^\s*→ 사용자[^:\n]*:\s*$", text, re.M))
        recent = time.time() - f.stat().st_mtime < 2 * 86400   # 옛 txt 는 끝 표시가 없다 — 이틀 손 안 대면 끝으로 본다
        return {"id": tid, "kind": "txt", "title": text.split("\n", 1)[0].strip()[:80] or f.name,
                "session": "txt 문서 · " + str(f.parent).replace(str(HOME), "~"),
                "status": "진행 중" if mine or re.search(r"^\s*\[[ ~]\]", text, re.M) or recent else "끝",
                "mine": mine, "waiting": 0, "mtime": f.stat().st_mtime,
                "asks": [m.group(1).strip() for m in re.finditer(r"^\s*\[ \]\s*(.+)$", text, re.M)]}
    meta, body = split(text)
    items = [b for b in parse(body) if b["type"] == "item"]
    status = "끝" if meta.get("status", "") in DONE else "진행 중"
    mine, waiting, now = sum(i["turn"] == "me" for i in items), sum(i["turn"] == "claude" for i in items), doing(brief(body))
    # 멈춤: 진행 중인데 세션은 일 안 하고(쉼·꺼짐) 사용자에게 열린 질문도 없다 — 세션이 규칙을 지켰는지와 상관없이 사실로 판정한다.
    # 11-12 Steam: 사용자 할 일("5분 실행")이 닫은 질문 답에만 있어 '내 차례 없음'으로 떴다. 남이 할 일만 기다리면 `지금: … 기다림`(기존 규칙).
    stuck = status == "진행 중" and not mine and not waiting and live().get(meta.get("sid", "")) != "busy" and "기다림" not in (now or "")
    return {"id": tid, "kind": "task", "title": meta.get("title") or tid, "session": meta.get("session", ""),
            "status": status, "stuck": stuck,
            "mine": mine or int(stuck), "waiting": waiting,
            "asks": [f"{i['id']}. {i['title']}" for i in items if i["turn"] == "me"],
            "listening": tid in listeners() or bool(chan_targets(meta)), "unread": unread(d), "mtime": f.stat().st_mtime,
            "now": now, "brief": brief(body)[:600], "live": live().get(meta.get("sid", "")),
            "bell": (d / ".bell").exists(), "local": is_local(meta)}


def all_ids():
    ids = [p.name for p in sorted(BOARD.iterdir()) if (p / "task.md").is_file()] if BOARD.is_dir() else []
    seen = set()
    for d in EXTRA:
        for f in sorted(d.glob("*.txt")) if d.is_dir() else []:
            if f.name not in seen:
                seen.add(f.name)
                ids.append("txt:" + f.name)
    return ids


def search(q, hide=lambda tid: False):
    """문서 전체(제목·요약·질문·답·메모)에서 찾기 — 끝난 작업까지(11-12 사용자 "검색기능 … 끝난작업까지 포함해서").
    빈칸으로 나눈 낱말이 모두 들어 있는 작업만, 대소문자 무시. 본문에서 처음 걸린 줄을 미리보기로 준다. 최근 고친 순."""
    terms = [t.lower() for t in q.split()][:8]
    out = []
    for tid in all_ids() if terms else []:
        try:
            f, _ = resolve(tid)
            text = f.read_text(encoding="utf-8", errors="replace")
            if not all(t in text.lower() for t in terms) or hide(tid):
                continue
        except (KeyError, OSError):
            continue
        body = text if tid.startswith("txt:") else split(text)[1]
        line = next((ln for ln in body.splitlines() if any(t in ln.lower() for t in terms)), "")
        line = re.sub(r"^\s*(?:-> |⇒ |#+\s*(?:\[[ ~v]\]\s*)?|[-*]\s+)", "", line).strip()
        i = min((line.lower().find(t) for t in terms if t in line.lower()), default=0)
        snip = line[max(0, i - 40):i + 100]
        out.append({"id": tid, "snippet": ("…" if i > 40 else "") + snip + ("…" if i + 100 < len(line) else ""), "mtime": f.stat().st_mtime})
    return sorted(out, key=lambda x: -x["mtime"])


def detail(tid):
    f, d = resolve(tid)
    text = f.read_text(encoding="utf-8", errors="replace")
    out = summary(tid) | {"raw": text}
    if d is not None:
        meta, body = split(text)
        out["blocks"] = parse(body)
        stamp(d, out["blocks"], f.stat().st_mtime)
    return out


def stamp(d, blocks, mtime, first=False):
    """말마다 받은 시각을 entries 에 `at` 으로 단다(11-11 사용자 "언제 받은건지 알수가 없어").
    문서엔 시각이 없다(세션은 Edit 로 `⇒` 를 쓴다) — 서버가 문서가 바뀔 때마다 새 말을 그 수정 시각으로 `.times` 에 적어 둔다.
    키 = 질문·누구·글·같은 글 몇 번째. 글을 고치면 새 말로 친다. first(서버 시작) 때 `.times` 가 없는 옛 문서의 말은 '' = 모름."""
    f = d / ".times"
    try:
        old = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        old = None
    at = "" if old is None and first else datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
    cur, n = {}, collections.Counter()
    for b in blocks:
        for e in b.get("entries", ()):
            k = f"{b.get('id', MEMO)}|{e['who']}|{e['text']}"
            n[k] += 1
            key = hashlib.sha1(f"{k}|{n[k]}".encode()).hexdigest()[:12]
            e["at"] = cur[key] = (old or {}).get(key, at)
    if cur != old:
        write(f, json.dumps(cur))


_stamped = {}


def stamp_all(first=False):
    """바뀐 문서만 다시 읽어 새 말에 시각을 단다 — chan_loop 가 1.5초마다(세션이 쓴 순간에 가깝게)."""
    for tid in all_ids():
        f = BOARD / tid / "task.md"
        if tid.startswith("txt:") or not f.is_file():
            continue
        m = f.stat().st_mtime
        if _stamped.get(tid) != m:
            _stamped[tid] = m
            stamp(f.parent, parse(split(f.read_text(encoding="utf-8", errors="replace"))[1]), m, first)


def send(d, items):
    (d / ".sent").write_text(json.dumps({"at": now(), "items": items}, ensure_ascii=False), encoding="utf-8")


def slug(title):
    """제목 → 작업 이름(폴더). 한글·영숫자만 남기고 나머진 '-', 겹치면 -2, -3."""
    base = re.sub(r"[^0-9A-Za-z가-힣]+", "-", title).strip("-")[:40].strip("-") or "task"
    tid, n = base, 2
    while (BOARD / tid).exists():
        tid, n = f"{base}-{n}", n + 1
    return tid


def assign(sid, title, text, bell=False):
    """[＋ 새 작업] — 사용자가 고른 세션에 작업 문서를 만들어 주고 채널로 바로 깨운다(11-12 사용자 "테스크 보드에서 세션이랑 주제 추가해서 테스크 만드는거").
    할 일은 `## 메모` 에 사용자 말(->)로, 머리 `from: board`(세션이 첫 답을 달기 전까지 배달 글에 '새 작업' 안내). 붙인 사진은 대기실 → 작업 img/."""
    s = next((s for _, _, s in chan_info()[0] if s.get("sessionId") == sid), None)
    if not s:
        raise KeyError("그 세션은 지금 깨울 수 없습니다 — 목록을 새로 불러 주세요")
    title = " ".join(title.split())[:120]
    if not title or not text.strip():
        raise ValueError("제목과 할 일을 적어 주세요")
    tid = slug(title)
    d = BOARD / tid
    d.mkdir(parents=True)
    for rel in set(re.findall(r"!\[[^\]]*\]\((img/[^)\s/]+)\)", text)):
        src = BOARD / ".staging" / rel
        if src.is_file():
            (d / "img").mkdir(exist_ok=True)
            os.replace(src, d / rel)
    doc = TEMPLATE.format(title=title, session=s.get("name", ""), summary="사용자가 Task Board 에서 맡김 — 메모의 할 일을 읽고 시작 전")
    write(d / "task.md", add_answer(set_meta(set_meta(doc, "sid", sid), "from", "board"), MEMO, text))
    if bell:
        (d / ".bell").touch()
    send(d, [MEMO])
    return tid


def is_local(meta):
    """문서 머리 `local: yes` — 회사 내부 자료(결재 번호·금액·사람 이름 등)가 있어 밖에서 보는 문(터널)으로는 안 보여 주는 작업.
    2026-11-04 회사 결재 문서를 다루는 작업에서 요청됨(그땐 아티팩트로 안 보내기). 이 PC 화면엔 그대로 보인다."""
    return meta.get("local", "").lower() in ("yes", "true", "1", "예")


# ── 슬랙: 밖에서 볼 때 로그인 코드 · 🔔 끝 알림 ──────────────────────────────
_slack = {}


def slack(text):
    """BOARD_SLACK_EMAIL 의 슬랙 DM 으로 보낸다 — 운영보드 로그인과 같은 길(users.lookupByEmail → chat.postMessage)."""
    if not (SLACK_TOKEN and (SLACK_EMAIL or SLACK_USER)):
        raise RuntimeError("슬랙 설정 없음(SLACK_BOT_TOKEN · BOARD_SLACK_EMAIL 또는 BOARD_SLACK_USER)")

    def call(method, **params):
        req = urllib.request.Request("https://slack.com/api/" + method, data=urllib.parse.urlencode(params).encode(),
                                     headers={"Authorization": "Bearer " + SLACK_TOKEN})
        with urllib.request.urlopen(req, timeout=10) as r:
            j = json.load(r)
        if not j.get("ok"):
            raise RuntimeError(f"슬랙 {method}: {j.get('error')}")
        return j
    if "uid" not in _slack:
        _slack["uid"] = SLACK_USER or call("users.lookupByEmail", email=SLACK_EMAIL)["user"]["id"]
    call("chat.postMessage", channel=_slack["uid"], text=text)


_rang = {}   # 작업 → 마지막으로 보내 본 시각 — 슬랙이 실패하면 1분 뒤 다시


def ring_done():
    """🔔 를 누른 작업(`.bell`)이 끝이 되면 슬랙 DM 한 번 보내고 `.bell` 을 지운다 — chan_loop 가 돈다.
    지금 알림은 이것 하나뿐이다(11-12 사용자 "아예 알림빼고 … 알림버튼 눌러서 그 테스크 끝나면 알림오도록")."""
    for f in list(BOARD.glob("*/.bell")):
        d = f.parent
        try:
            meta, body = split((d / "task.md").read_text(encoding="utf-8", errors="replace"))
        except OSError:
            f.unlink(missing_ok=True)
            continue
        if meta.get("status", "") not in DONE or time.time() - _rang.get(d.name, 0) < 60:
            continue
        _rang[d.name] = time.time()
        try:
            slack(f"✅ Task Board · {meta.get('title') or d.name} — 끝났습니다\n{doing(brief(body))}\n{url(d.name, public=True)}")
            f.unlink(missing_ok=True)
            print(f"[🔔] {d.name} 슬랙 보냄", flush=True)
        except (OSError, RuntimeError, ValueError, KeyError) as e:
            print(f"[🔔] {d.name}: {e}", flush=True)


_done_seen = {}           # 작업 → (문서 mtime, 끝이었나) — 진행 중 → 끝 으로 바뀌는 순간만 잡는다
_pops = queue.Queue()


def watch_done(first=False):
    """작업이 끝이 되는 순간 이 PC 화면 위에 배너(popup.py) — 모든 작업(🔔 와 상관없이). 서버 시작 때(first)는 기록만 한다.
    11-12 사용자 "세션이 끝날 때마다 알림이 … 너무 자주" → 턴마다 뜨던 turn-notify 대신 작업이 끝날 때만, "엄청 화려하게 · 세션마다 달라도"."""
    for tid in all_ids():
        f = BOARD / tid / "task.md"
        if tid.startswith("txt:") or not f.is_file():
            continue
        m = f.stat().st_mtime
        old = _done_seen.get(tid)
        if old and old[0] == m:
            continue
        meta, body = split(f.read_text(encoding="utf-8", errors="replace"))
        done = meta.get("status", "") in DONE
        _done_seen[tid] = (m, done)
        if done and not first and old is not None and not old[1] and POPUP:
            _pops.put({"title": meta.get("title") or tid, "session": meta.get("session", ""), "now": doing(brief(body)),
                       "at": datetime.datetime.now().strftime("%H:%M"), "url": url(tid), "theme": banner_theme(meta.get("session", ""))})


def short_name(session):
    return re.sub(r"\s*\[[0-9a-f]+\]\s*$", "", session or "").strip()


def banner_file(session):
    """세션이 꾸민 '끝' 배너 — $BOARD_DIR/.banners/<세션 이름>.json(11-12 사용자 "각 세션한테 배너 디자인하라그래 자기 색깔에 맞게").
    세션마다 파일 하나라 14곳이 동시에 고쳐도 안 부딪친다. 이름 기준 — 다시 띄워 sid·ref 가 바뀌어도 그대로."""
    return BOARD / ".banners" / ((re.sub(r"[/\\\0]", "_", short_name(session)) or "unknown") + ".json")


def banner_theme(session):
    """{emoji, colors[3], heads[], confetti[], css} 중 있는 것 — 잘못된 파일이면 기본 모양(빈 dict)."""
    try:
        f = banner_file(session)
        v = json.loads(f.read_text(encoding="utf-8")) if f.stat().st_size < 20_000 else {}
        return v if isinstance(v, dict) else {}
    except (OSError, ValueError):
        return {}


def pop_loop():
    """배너를 하나씩 — 둘이 겹치지 않게 앞의 것이 닫힌 뒤 다음 것."""
    here = pathlib.Path(__file__).resolve().parent
    while True:
        data = _pops.get()
        try:
            subprocess.run([sys.executable, str(here / "popup.py"), json.dumps(data, ensure_ascii=False)],
                           timeout=60, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired) as e:
            print(f"[배너] {e}", flush=True)


def _logins():
    """로그인한 기기 — sha256(쿠키 값) → 만료 시각. 쿠키 값 자체는 어디에도 남기지 않는다."""
    try:
        v = json.loads(LOGINS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: e for k, e in v.items() if e > time.time()}


def _save_logins(v):
    LOGINS.parent.mkdir(parents=True, exist_ok=True)
    tmp = LOGINS.with_name("." + LOGINS.name + ".tmp")
    tmp.write_text(json.dumps(v), encoding="utf-8")
    tmp.chmod(0o600)
    os.replace(tmp, LOGINS)


def logged_in(token):
    return bool(token) and hashlib.sha256(token.encode()).hexdigest() in _logins()


# ── 채널: [보내기]를 세션에 바로 넣는다 ──────────────────────────────────────
# 세션은 이 서버의 /mcp(HTTP MCP, 이름 board)에 붙고, `--dangerously-load-development-channels server:board` 로 띄운
# 세션만 Claude Code 가 받아 깨어난다. 세션마다 프로세스를 띄우지 않는다 — 이 서버 하나가 모든 연결을 맡는다.
CHAN = "board"
CHAN_HELP = ('Task Board 에서 사용자가 [보내기]를 누르면 <channel source="board" task="<작업>"> 로 그 작업의 답·메모가 온다. '
             "사용자가 직접 보낸 것이니 채팅 답과 같이 다룬다 — 본문 끝 안내대로 task.md 를 다시 읽고 반영한다. "
             "이 세션은 board wait 를 걸지 않는다.")
IN_SERVER = False
_chans = {}            # MCP 세션 id → {"pid": Claude 프로세스, "q": 열린 스트림의 큐 또는 None}
_chans_lock = threading.Lock()
_cinfo = {"at": 0.0, "v": ([], {})}


def _proc(pid, name):
    return pathlib.Path(f"/proc/{pid}/{name}").read_bytes()


def claude_session(pid):
    """Claude Code 가 <설정 폴더>/sessions/<pid>.json 에 적는 세션 정보(sessionId·name). 아니면 None."""
    try:
        env = dict(x.split("=", 1) for x in _proc(pid, "environ").decode(errors="replace").split("\0") if "=" in x)
        cfg = pathlib.Path(env.get("CLAUDE_CONFIG_DIR") or HOME / ".claude")
        return json.loads((cfg / "sessions" / f"{pid}.json").read_text(encoding="utf-8")) | {"_dir": str(cfg)}
    except (OSError, ValueError, TypeError):
        return None


def claude_pid(pid=None):
    """이 프로세스를 띄운 Claude 세션의 pid — 부모를 거슬러 올라가 세션 파일이 있는 곳."""
    pid = pid or os.getppid()
    for _ in range(8):
        if pid <= 1:
            return None
        if claude_session(pid):
            return pid
        try:
            pid = int(_proc(pid, "stat").decode().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            return None
    return None


def chan_on(pid):
    """채널을 켜고 띄운 세션인가 — 플래그 없이 붙은 연결에 넣으면 Claude Code 가 조용히 버린다."""
    try:
        return f"server:{CHAN}".encode() in _proc(pid, "cmdline").split(b"\0")
    except OSError:
        return False


def chan_info():
    """([(MCP 세션 id, pid, 세션 정보)] 넣을 수 있는 연결, {세션 이름: 살아 있는 수}). 2초 캐시."""
    if time.time() - _cinfo["at"] < 2:
        return _cinfo["v"]
    if IN_SERVER:
        with _chans_lock:
            conns = [(m, c["pid"]) for m, c in _chans.items() if c["q"] is not None]
    else:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/channels", timeout=2) as r:
                conns = [tuple(x) for x in json.load(r)]
        except (OSError, ValueError):
            conns = []
    live = [(m, p, s) for m, p in conns if chan_on(p) and (s := claude_session(p))]
    names = {}
    for sd in {pathlib.Path(s["_dir"]) for _, _, s in live} | {HOME / ".claude"}:
        for f in (sd / "sessions").glob("*.json"):
            if pathlib.Path(f"/proc/{f.stem}").exists():
                try:
                    n = json.loads(f.read_text(encoding="utf-8")).get("name", "")
                except (OSError, ValueError):
                    continue
                names[n] = names.get(n, 0) + 1
    _cinfo.update(at=time.time(), v=(live, names))
    return live, names


def chan_targets(meta):
    """이 작업을 받을 연결 — 문서의 sid 와 같은 세션, 없으면 이름이 하나뿐인 세션(같은 이름이 여럿이면 안 보낸다: 잘못 가느니 안 간다)."""
    live, names = chan_info()
    sid = meta.get("sid", "")
    hit = [m for m, _, s in live if sid and s.get("sessionId") == sid]
    name = re.sub(r"\s*\[[0-9a-f]+\]\s*$", "", meta.get("session", ""))
    if not hit and name and names.get(name) == 1:
        hit = [m for m, _, s in live if s.get("name") == name]
    return hit


def chan_here():
    """이 명령을 부른 세션이 채널로 [보내기]를 바로 받는가."""
    pid = claude_pid()
    return bool(pid) and any(p == pid for _, p, _ in chan_info()[0])


def wait_hint(tids):
    if chan_here():
        return "이 세션은 채널로 연결돼 있어 [보내기]가 바로 들어온다 — board wait 는 걸지 않는다."
    return f"이 세션은 채널이 없다 — board wait {' '.join(tids)} 를 Bash run_in_background 로 건다(채널을 켜고 다시 띄우면 필요 없다)."


def claim(f):
    """문서에 이 세션 id 를 적는다 — 채널이 이름 대신 id 로 세션을 찾게(이름은 겹치고 바뀐다)."""
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    text = f.read_text(encoding="utf-8")
    if sid and split(text)[0].get("sid") != sid:
        write(f, set_meta(text, "sid", sid))


def _chan_put(hits, tid, content):
    msg = {"jsonrpc": "2.0", "method": "notifications/claude/channel", "params": {"content": content, "meta": {"task": tid}}}
    sent = 0
    with _chans_lock:
        for m in hits:
            if _chans.get(m, {}).get("q") is not None:
                _chans[m]["q"].put(msg)
                sent += 1
    return sent


def deliver():
    """안 읽힌 [보내기]를 그 작업 세션에 넣고 읽음 처리. 채널이 없는 세션 것은 그대로 둔다(board wait 가 받는다)."""
    for tid in all_ids():
        if tid.startswith("txt:"):
            continue
        try:
            f, d = resolve(tid)
            if not unread(d):
                continue
            hits = chan_targets(split(f.read_text(encoding="utf-8", errors="replace"))[0])
            if hits and (sent := _chan_put(hits, tid, report_text(tid, d, channel=True))):
                (d / ".seen").write_text(now(), encoding="utf-8")
                print(f"[채널] {tid} → {sent}곳", flush=True)
        except (KeyError, OSError, ValueError) as e:
            print(f"[채널] {tid}: {e}", flush=True)


def chan_loop(interval=1.5):
    while True:
        time.sleep(interval)
        try:
            deliver()
            stamp_all()
            watch_done()
            ring_done()
            with _chans_lock:
                for m in [m for m, c in _chans.items() if c["q"] is None and not pathlib.Path(f"/proc/{c['pid']}").exists()]:
                    del _chans[m]
        except Exception as e:   # 이 줄이 죽으면 [보내기]가 다시 아무에게도 안 간다
            print(f"[채널] {e!r}", flush=True)


# ── 서버 ─────────────────────────────────────────────────────────────────────

def usage():
    """Claude 한도(5시간·7일) — 화면 하단 한 줄(11-12 사용자 "전체 사용량만", "남은 기간"). 상태줄(~/.claude/statusline.sh)이
    세션마다 USAGE/<sid>.json 에 rate_limits 를 남긴다. 창마다 가장 늦은 초기화 시각의 값 중 최댓값을 고른다 — 쉬던 세션이
    옛 값으로 다시 그려도 덮이지 않게(같은 창에선 사용량이 줄지 않는다)."""
    now, best = time.time(), {}
    for f in USAGE.glob("*.json"):
        try:
            mt = f.stat().st_mtime
            if now - mt > 8 * 86400:   # 7일 창보다 오래된 세션 파일은 쓸모없다
                f.unlink()
                continue
            d = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        for k in ("five_hour", "seven_day"):
            try:
                r, pct = float(d[k]["resets_at"]), float(d[k]["used_percentage"])
            except (KeyError, TypeError, ValueError):   # 그 창이 없는 요금제(울트라는 5시간 창이 없다)·빈 값
                continue
            if r <= now:   # 지난 창의 옛 값 — 새 값이 올 때까지 안 보인다('초기화됨'이 남아 있지 않게)
                continue
            b = best.get(k)
            if b is None or r > b["resets_at"] + 60 or (abs(r - b["resets_at"]) <= 60 and pct > b["used_percentage"]):
                best[k] = {"resets_at": r, "used_percentage": pct, "at": mt}
    return best


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, body, ctype="application/json; charset=utf-8", code=200, headers=None):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def public(self):
        """밖에서 보는 문(BOARD_PUBLIC_PORT)으로 온 요청인가 — 그 문은 Cloudflare 터널만 붙는다(127.0.0.1 에만 연다)."""
        return getattr(self.server, "public", False)

    def ip(self):
        return (self.headers.get("Cf-Connecting-Ip") if self.public() else None) or self.client_address[0]

    def cookie(self):
        c = http.cookies.SimpleCookie()
        try:
            c.load(self.headers.get("Cookie") or "")
        except http.cookies.CookieError:
            return ""
        return c["board"].value if "board" in c else ""

    def page(self, tpl, msg="", code=200):
        self.reply(tpl.replace("<!--e-->", msg).encode(), "text/html; charset=utf-8", code=code)

    def gate(self, path):
        """이 PC 문(BOARD_PORT, 127.0.0.1 만)은 그대로. 밖의 문은 슬랙 코드로 로그인한 기기만, 채널(/mcp)은 로그인해도 안 된다 —
        [보내기]가 세션 15개에 '사용자 말'로 들어가고 세션은 이 PC 에서 명령을 실행한다."""
        if not self.public():
            # 이 PC 문도 브라우저로 연 다른 사이트가 두드릴 수 있다 — text/plain POST 는 미리 묻기(preflight) 없이 오고 서버는 본문을 JSON 으로 읽는다.
            # 그러면 아무 웹페이지가 [보내기]로 세션에 '사용자 말'을 넣는다(11-12 점검). 주소(Host)가 이 PC 이름이 아니면(DNS 리바인딩) 막고,
            # 쓰기 요청에 Origin 이 붙어 왔으면 이 PC 화면에서 온 것만 받는다(Claude Code·curl·board 명령은 Origin 을 안 붙인다).
            local = ("localhost", "127.0.0.1", "::1")
            host = urllib.parse.urlsplit("//" + (self.headers.get("Host") or "")).hostname
            origin = self.headers.get("Origin")
            if host not in local or (self.command != "GET" and origin is not None and urllib.parse.urlsplit(origin).hostname not in local):
                self.reply({"error": "이 PC 화면에서 온 요청이 아닙니다"}, code=403)
                return False
            return True
        if path in ("/mcp", "/api/channels"):
            self.reply({"error": "이 PC 에서만"}, code=403)
            return False
        origin = urllib.parse.urlparse(self.headers.get("Origin") or "").netloc
        if self.command == "POST" and origin and origin not in (self.headers.get("Host"), urllib.parse.urlparse(PUBLIC_URL).netloc):
            self.reply({"error": "다른 사이트에서 온 요청"}, code=403)   # 쿠키가 SameSite=Lax 라 원래 안 실리지만 한 겹 더
            return False
        if logged_in(self.cookie()):
            return True
        if path in ("/", "/index.html"):
            self.page(LOGIN)
        else:
            self.reply({"error": "로그인이 필요합니다 — 화면을 새로고침해 주세요"}, code=401)
        return False

    def login_code(self):
        """슬랙 DM 으로 6자리 코드 — 1분에 한 번, 1시간에 10번까지(아무나 눌러 슬랙을 울리지 못하게)."""
        t = time.time()
        with _auth_lock:
            sent = [s for s in _auth["sent"] if t - s < 3600]
            if sent and t - sent[-1] < 60:
                return self.page(CODE, "방금 보냈습니다 — 슬랙 DM 을 확인해 주세요")
            if len(sent) >= 10:
                return self.page(LOGIN, "1시간에 10번까지입니다 — 잠시 뒤 다시", 429)
            code = f"{secrets.randbelow(10 ** 6):06d}"
            try:
                slack(f"Task Board 로그인 코드: {code} (5분 안에 입력). 직접 요청하지 않았다면 무시하세요 — 코드 없이는 못 들어옵니다.")
            except (OSError, RuntimeError, ValueError, KeyError) as e:
                print(f"[로그인] 슬랙 실패: {e}", flush=True)
                return self.page(LOGIN, "슬랙으로 못 보냈습니다 — PC 의 서버 로그를 봐 주세요", 502)
            _auth.update(code=hashlib.sha256(code.encode()).hexdigest(), exp=t + 300, tries=0, sent=sent + [t])
        self.page(CODE)

    def login(self, raw):
        """코드가 맞으면 이 기기에 3달짜리 쿠키. 한 코드에 5번 틀리면 그 코드는 버리고, 같은 IP 가 5번 틀리면 10분 막는다."""
        ip, t = self.ip(), time.time()
        n, until = _fails.get(ip, (0, 0))
        if t < until:
            return self.page(CODE, "여러 번 틀려 10분 동안 막혔습니다", 429)
        code = re.sub(r"\D", "", dict(urllib.parse.parse_qsl(raw.decode(errors="replace"))).get("code", ""))
        with _auth_lock:
            ok = bool(_auth["code"]) and t < _auth["exp"] and hmac.compare_digest(hashlib.sha256(code.encode()).hexdigest(), _auth["code"])
            if ok:
                _auth["code"] = ""
            else:
                _auth["tries"] += 1
                if _auth["tries"] >= 5:
                    _auth["code"] = ""
            if ok:
                tok = secrets.token_urlsafe(32)
                v = _logins()
                v[hashlib.sha256(tok.encode()).hexdigest()] = t + KEEP
                _save_logins(v)
        if not ok:
            _fails[ip] = (0, t + 600) if n + 1 >= 5 else (n + 1, 0)
            return self.page(CODE, "코드가 틀렸거나 지났습니다", 401)
        _fails.pop(ip, None)
        self.reply(b"", code=303, headers={
            "Location": "/", "Set-Cookie": f"board={tok}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age={KEEP}"})

    def hidden(self, tid):
        """밖의 문에선 `local: yes` 작업을 없는 작업처럼."""
        return self.public() and not tid.startswith("txt:") and is_local(split(resolve(tid)[0].read_text(encoding="utf-8", errors="replace"))[0])

    def mcp_post(self, raw):
        """board MCP — 도구 없이 채널만. 처음 붙을 때 Claude Code 가 X-Board-Pid(board channel-headers)로 자기 pid 를 알린다."""
        msg = json.loads(raw or b"{}")
        if not isinstance(msg, dict) or msg.get("id") is None:      # 알림·응답·묶음은 받기만
            return self.reply(b"", code=202)
        m, mid, hdr = msg.get("method"), msg["id"], {}
        if m == "initialize":
            sid = os.urandom(16).hex()
            try:
                pid = int(self.headers.get("X-Board-Pid") or 0)
            except ValueError:
                pid = 0
            with _chans_lock:
                _chans[sid] = {"pid": pid, "q": None}
            hdr["Mcp-Session-Id"] = sid
            # 2026-07-28 판(modern)은 서버가 먼저 보낼 길이 없어 Claude Code 가 채널을 안 붙인다 — 그 전 판으로 답한다
            out = {"protocolVersion": "2025-06-18", "capabilities": {"experimental": {"claude/channel": {}}},
                   "serverInfo": {"name": CHAN, "version": "1"}, "instructions": CHAN_HELP}
            out = {"jsonrpc": "2.0", "id": mid, "result": out}
        elif m == "ping":
            out = {"jsonrpc": "2.0", "id": mid, "result": {}}
        else:
            out = {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"없는 메서드: {m}"}}
        self.reply(out, headers=hdr)

    def mcp_stream(self):
        """서버 → 세션 스트림(SSE). 연결마다 스레드 하나가 큐를 기다린다 — 세션 쪽 프로세스는 없다."""
        sid = self.headers.get("Mcp-Session-Id", "")
        try:
            pid = int(self.headers.get("X-Board-Pid") or 0)
        except ValueError:
            pid = 0
        with _chans_lock:
            c = _chans.get(sid)
            if c is None and sid and pid > 0 and pathlib.Path(f"/proc/{pid}").exists():
                # 서버가 다시 켜진 뒤 세션이 옛 세션 id 로 스트림만 다시 연다 — 404 를 주면 Claude Code 는 두 번 해 보고 포기한다
                # ("SSE GET-stream reconnection exhausted", 11-09 install.sh 재시작에 15곳 전부 끊김). 머리글의 pid 로 받아 준다.
                c = _chans[sid] = {"pid": pid, "q": None}
                print(f"[채널] 다시 붙음 pid {pid}", flush=True)
            if c is not None:
                q = c["q"] = queue.Queue()
        if c is None:
            return self.reply({"error": "없는 MCP 세션 — 다시 initialize"}, code=404)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            self.wfile.write(b": ok\n\n")
            while True:
                self.wfile.flush()
                try:
                    ev = q.get(timeout=25)
                    self.wfile.write(b"event: message\ndata: " + json.dumps(ev, ensure_ascii=False).encode() + b"\n\n")
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
        except OSError:
            pass
        finally:
            with _chans_lock:
                if _chans.get(sid, {}).get("q") is q:
                    _chans[sid]["q"] = None

    def do_DELETE(self):
        if not self.gate("/mcp"):
            return
        with _chans_lock:
            _chans.pop(self.headers.get("Mcp-Session-Id", ""), None)
        self.reply(b"", code=200)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        if not self.gate(u.path):
            return
        try:
            if u.path == "/mcp":
                return self.mcp_stream()
            if u.path == "/api/channels":
                with _chans_lock:
                    return self.reply([[m, c["pid"]] for m, c in _chans.items() if c["q"] is not None])
            if u.path in ("/", "/index.html"):
                return self.reply((WEB / "index.html").read_bytes(), "text/html; charset=utf-8")
            if u.path == "/api/sessions":   # [＋ 새 작업] 에서 고를 세션 — 채널로 지금 깨울 수 있는 것만
                lv, out = live(), {}
                for _, _, ss in chan_info()[0]:
                    if ss.get("sessionId") and ss["sessionId"] not in out:
                        out[ss["sessionId"]] = {"sid": ss["sessionId"], "name": ss.get("name", ""), "live": lv.get(ss["sessionId"])}
                return self.reply(sorted(out.values(), key=lambda x: x["name"]))
            if u.path == "/api/search":
                return self.reply(search(q.get("q", ""), self.hidden))
            if u.path == "/api/usage":
                return self.reply(usage())
            if u.path == "/api/tasks":
                return self.reply([s for s in map(summary, all_ids()) if not (self.public() and s.get("local"))])
            if u.path == "/api/task":
                if self.hidden(q["id"]):
                    raise KeyError(q["id"])
                return self.reply(detail(q["id"]))
            if u.path.startswith("/files/"):
                tid, _, rel = urllib.parse.unquote(u.path[len("/files/"):]).partition("/")
                if tid == "_new":   # 새 작업 칸에 붙인 사진(맡기기 전 대기실)
                    d = BOARD / ".staging"
                elif self.hidden(tid):
                    raise KeyError(tid)
                else:
                    _, d = resolve(tid)
                f = (d / rel).resolve()
                if d.resolve() in f.parents and f.is_file():
                    return self.reply(f.read_bytes(), mimetypes.guess_type(f.name)[0] or "application/octet-stream")
        except (KeyError, StopIteration, TypeError, AttributeError):
            pass
        self.reply({"error": "없는 작업·파일"}, code=404)

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        n = int(self.headers.get("Content-Length") or 0)
        if u.path in ("/login", "/login/code"):
            raw = self.rfile.read(min(n, 4096))
            if not self.public():
                return self.reply(b"", code=303, headers={"Location": "/"})
            return self.login(raw) if u.path == "/login" else self.login_code()
        if not self.gate(u.path):
            return
        raw = self.rfile.read(n)
        try:
            if u.path == "/mcp":
                return self.mcp_post(raw)
            if u.path == "/api/logout-all":
                with _auth_lock:
                    _save_logins({})
                return self.reply({"ok": True})
            if u.path == "/api/new":
                b = json.loads(raw or b"{}")
                return self.reply({"id": assign(b.get("sid", ""), b.get("title", ""), b.get("text", ""), bool(b.get("bell")))})
            if u.path == "/api/image":
                if q["id"] == "_new":
                    d = BOARD / ".staging"
                elif self.hidden(q["id"]):
                    raise KeyError(q["id"])
                else:
                    _, d = resolve(q["id"])
                ext = re.sub(r"[^a-z0-9]", "", q.get("ext", "png").lower())[:5] or "png"
                (d / "img").mkdir(parents=True, exist_ok=True)
                name = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f") + "." + ext
                (d / "img" / name).write_bytes(raw)
                return self.reply({"path": "img/" + name})
            body = json.loads(raw or b"{}")
            tid = body["id"]
            if self.hidden(tid):
                raise KeyError(tid)
            f, d = resolve(tid)
            text = f.read_text(encoding="utf-8", errors="replace")
            if u.path == "/api/bell":
                if d is None:
                    raise KeyError("txt 문서엔 🔔 가 없습니다")
                (d / ".bell").touch() if body.get("on") else (d / ".bell").unlink(missing_ok=True)
                return self.reply(detail(tid))
            if u.path == "/api/raw":
                if f.stat().st_mtime != body.get("mtime"):
                    return self.reply({"error": "그사이 문서가 바뀌었습니다 — 다시 불러온 뒤 고쳐 주세요"}, code=409)
                new = body["text"]
            elif u.path == "/api/answer":
                new = add_answer(text, body["item"], body["text"])
            elif u.path == "/api/status":
                new = set_meta(text, "status", "끝" if body.get("done") else "진행 중")
            else:
                raise KeyError(u.path)
            if d is None and u.path != "/api/raw":
                return self.reply({"error": "txt 문서는 원문 편집만 됩니다"}, code=400)
            if new != text:
                write(f, new)
            if body.get("send") and d is not None:
                send(d, [body.get("item") or MEMO])
            return self.reply(detail(tid))
        except (KeyError, StopIteration, ValueError) as e:
            return self.reply({"error": f"요청 오류: {e}"}, code=400)


def serve():
    global IN_SERVER
    IN_SERVER = True
    BOARD.mkdir(parents=True, exist_ok=True)
    stamp_all(first=True)
    watch_done(first=True)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=chan_loop, daemon=True).start()
    threading.Thread(target=pop_loop, daemon=True).start()
    msg = f"Task Board: http://localhost:{PORT}  (문서 {BOARD})"
    if SLACK_TOKEN and (SLACK_EMAIL or SLACK_USER):   # 밖의 문 — 슬랙 로그인이 없으면 열지 않는다(열어도 아무도 못 들어오지만)
        pub = http.server.ThreadingHTTPServer(("127.0.0.1", PUBLIC_PORT), Handler)
        pub.public = True
        threading.Thread(target=pub.serve_forever, daemon=True).start()
        msg += f" · 밖의 문 127.0.0.1:{PUBLIC_PORT}" + (f" → {PUBLIC_URL}" if PUBLIC_URL else "") + " (슬랙 코드 로그인)"
    print(msg, flush=True)
    srv.serve_forever()


# ── 명령 ─────────────────────────────────────────────────────────────────────

TEMPLATE = """---
title: {title}
session: {session}
status: 진행 중
---
## 요약
지금: {summary}

## 정할 것

## 알려 드릴 것

## 메모
"""


def url(tid="", public=False):
    base = PUBLIC_URL if public and PUBLIC_URL else f"http://localhost:{PORT}"
    return base + "/" + (("#" + urllib.parse.quote(tid)) if tid else "")


def cmd_new(a):
    d = BOARD / a.task
    if "/" in a.task or a.task.startswith("."):
        sys.exit("작업 이름에 / 나 맨 앞 . 은 쓸 수 없습니다")
    if (d / "task.md").exists():
        sys.exit(f"이미 있습니다: {d / 'task.md'}")
    d.mkdir(parents=True, exist_ok=True)
    write(d / "task.md", TEMPLATE.format(title=a.title, session=a.session, summary=a.summary or "막 시작함"))
    claim(d / "task.md")
    print(f"{d / 'task.md'}\n{url(a.task)}\n{wait_hint([a.task])}")


def report_text(tid, d, channel=False):
    info = json.loads((d / ".sent").read_text(encoding="utf-8"))
    out = [f"[보냄 {info.get('at', '')}] {tid} — {d / 'task.md'}"]
    meta, body = split((d / "task.md").read_text(encoding="utf-8"))
    blocks = parse(body)
    if meta.get("from") == "board" and not any(e["who"] == "claude" for b in blocks if b["type"] == "memo" for e in b["entries"]):
        out.append("  (새 작업 — 사용자가 Task Board 에서 이 세션에 맡겼다. 메모의 할 일을 일로 받아 시작한다: 요약 `지금:` 을 고치고,"
                   " 진행·물을 것·결과는 이 문서에, 메모엔 ⇒ 로 '받았다' 한 줄. 끝나면 board done)")
    for b in blocks:
        if b["type"] == "item" and b["turn"] == "claude":
            out.append(f"  {b['id']}. {b['title']}\n    -> " + b["entries"][-1]["text"].replace("\n", "\n    -> "))
        if b["type"] == "memo" and MEMO in info.get("items", []) and b["entries"] and b["entries"][-1]["who"] == "me":
            out.append("  메모:\n    -> " + b["entries"][-1]["text"].replace("\n", "\n    -> "))
    out.append("→ task.md 를 다시 읽고 질문마다 ⇒ 로 반영(결정된 건 [v])."
               + (" 채널로 받았으니 board wait 는 걸지 않는다." if channel else " 그리고 board wait 를 다시 건다."))
    return "\n".join(out)


def report(tid, d):
    print(report_text(tid, d))


def cmd_wait(a):
    dirs = {}
    for t in a.task:
        _, d = resolve(t)
        if d is None:
            sys.exit(f"txt 문서는 기다릴 수 없습니다: {t}")
        dirs[t] = d
        claim(d / "task.md")
    if chan_here():
        print(wait_hint(a.task) + " 안 읽은 [보내기]가 있으면 곧 채널로 들어온다.")
        return 5
    deadline = time.time() + a.max * 60
    while time.time() < deadline:
        ready = []
        for t, d in dirs.items():
            if unread(d):
                ready.append((t, d))
        if ready:
            for t, d in ready:
                report(t, d)
                (d / ".seen").write_text(now(), encoding="utf-8")
            return 0
        time.sleep(a.poll)
    print(f"{a.max}분 동안 [보내기] 없음 — board wait {' '.join(a.task)} 를 다시 건다.")
    return 3


def cmd_done(a):
    f, d = resolve(a.task)
    write(f, set_meta(f.read_text(encoding="utf-8"), "status", "끝"))
    print(f"끝으로 표시: {a.task}")


def cmd_orphans(a):
    """보냈는데 듣는 세션이 없어 안 읽힌 작업 — 아무 세션이나 이걸 보고 그 세션에 SendMessage 로 깨워 준다."""
    found = 0
    for t in all_ids():
        s = summary(t)
        if s.get("unread") and not s.get("listening"):
            found += 1
            print(f"{t}\t{s['session']}\t{s['title'][:60]}")
            print(f"  SendMessage → {s['session']}: Task Board {t} 에 사용자가 [보내기] 했는데 이 세션의 board wait 가 꺼져 있어 못 깼습니다."
                  f" `board wait {t}` 를 Bash run_in_background 로 다시 걸어 주세요 — 바로 끝나며 보낸 내용을 보여 줍니다.")
    if not found:
        print("안 읽힌 작업 없음")


def cmd_setup(a):
    """처음 한 번(여러 번 해도 같음) — 설정 파일 · board 명령 링크 · PC 화면(리눅스 systemd 사용자 서비스) · 채널 MCP 등록.
    플러그인으로 깔았으면 세션 시작 훅이 이걸 안내한다. 이 PC 처럼 install.sh 로 깔면 install.sh 가 부른다."""
    here = pathlib.Path(__file__).resolve().parent
    cfgf = pathlib.Path(os.environ.get("XDG_CONFIG_HOME") or HOME / ".config") / "board" / "env"
    if not cfgf.is_file():
        cfgf.parent.mkdir(parents=True, exist_ok=True)
        write(cfgf, f"BOARD_DIR={HOME / 'board'}\nBOARD_PORT=8770\n"
                    "# 밖(폰)에서 보기 — 슬랙 코드 로그인 · 🔔 끝 알림. 둘 다 있으면 127.0.0.1:BOARD_PUBLIC_PORT 에 밖의 문이 열린다(터널을 붙인다)\n"
                    "# SLACK_BOT_TOKEN=\n# BOARD_SLACK_EMAIL=\n# BOARD_PUBLIC_URL=https://board.example.com\n")
        cfgf.chmod(0o600)
        print(f"설정 만듦: {cfgf}")
    bindir = pathlib.Path(os.environ.get("XDG_BIN_HOME") or HOME / ".local" / "bin")
    bindir.mkdir(parents=True, exist_ok=True)
    (here / "board.py").chmod(0o755)
    link = bindir / "board"
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(here / "board.py")
    print(f"명령: {link} → {here / 'board.py'}" + ("" if shutil.which("board") else f"  (⚠ {bindir} 가 PATH 에 없음)"))
    if sys.platform.startswith("linux") and shutil.which("systemctl") and not a.no_server:
        unit = pathlib.Path(os.environ.get("XDG_CONFIG_HOME") or HOME / ".config") / "systemd" / "user" / "board.service"
        unit.parent.mkdir(parents=True, exist_ok=True)
        write(unit, f"[Unit]\nDescription=board (Task Board)\n\n[Service]\nExecStart=/usr/bin/env python3 {here / 'board.py'} serve\nRestart=on-failure\n\n[Install]\nWantedBy=default.target\n")
        for cmd in (["daemon-reload"], ["enable", "board.service"], ["restart", "board.service"]):
            subprocess.run(["systemctl", "--user", *cmd], check=False, capture_output=True)
        print(f"PC 화면: http://localhost:{PORT}  (사용자 서비스 board)")
    elif not a.no_server:
        print(f"PC 화면은 수동 — `python3 {here / 'board.py'} serve` ")
    if shutil.which("claude") and sys.platform.startswith("linux"):   # 채널은 /proc 로 세션을 찾는다 — 리눅스만
        spec = {"type": "http", "url": f"http://127.0.0.1:{PORT}/mcp", "headersHelper": f"{link} channel-headers"}
        subprocess.run(["claude", "mcp", "remove", CHAN, "-s", "user"], capture_output=True)
        r = subprocess.run(["claude", "mcp", "add-json", CHAN, json.dumps(spec), "-s", "user"], capture_output=True, text=True)
        print(f"채널 MCP '{CHAN}' 등록" + (" 함" if r.returncode == 0 else f" 실패: {(r.stderr or r.stdout).strip()[:200]}"))
        print(f"  세션을 `claude --dangerously-load-development-channels server:{CHAN}` 로 띄우면 [보내기]가 바로 들어온다(board wait 불필요).")
        print(f"  매번 붙이기 싫으면 래퍼를 PATH 맨 앞에(대화형 실행에만 붙인다, -p·하위 명령은 그대로) — ~/.bashrc 와 ~/.profile 끝에:\n"
              f'    [ -x "{here / "bin" / "claude"}" ] && PATH="{here / "bin"}:$PATH"')


def cmd_channel_headers(a):
    """Claude Code 가 board MCP 에 붙을 때 부른다(headersHelper) — 이 세션의 Claude pid 를 머리글로 알린다."""
    print(json.dumps({"X-Board-Pid": str(claude_pid() or 0)}))


BANNER_TEMPLATE = {
    "emoji": "", "colors": [], "heads": [], "confetti": [], "css": "",
    "_도움말": ("있는 칸만 기본 모양을 덮는다. emoji = 메달 가운데 이모지 하나 · colors = 색 셋 [주, 보조, 밝은 강조] (#rrggbb) · "
              "heads = 머리말 후보(뜰 때마다 하나 고름, 짧게) · confetti = 색종이로 터질 이모지들 · "
              "css = 덧붙일 CSS(창은 640×230 고정, 뼈대: .wrap > .ring + .card > .badge(.halo .emo) .txt(.top(.chip .at) .head .title .now .stamp) .bar canvas#fx, "
              "색 변수 --a --b --c). 이 칸(_도움말)은 지워도 된다."),
}


def cmd_banner(a):
    """이 세션의 '끝' 배너 설정 파일을 만들고(없으면 틀로) 경로를 찍는다. --shot 이면 실제 창과 같은 엔진으로 PNG 두 장(터질 때·가라앉은 뒤)."""
    session = a.session
    if not session:
        pid = claude_pid()
        session = (claude_session(pid) or {}).get("name", "") if pid else ""
    if not short_name(session):
        sys.exit("세션 이름을 모릅니다 — --session \"이름\" 으로")
    f = banner_file(session)
    if not f.exists():
        f.parent.mkdir(parents=True, exist_ok=True)
        write(f, json.dumps(BANNER_TEMPLATE, ensure_ascii=False, indent=2) + "\n")
    print(f"배너 설정: {f}  (세션 '{short_name(session)}')")
    if not a.shot:
        return print("고친 뒤 `board banner --shot` 으로 모양을 본다(화면에 안 띄움).")
    here = pathlib.Path(__file__).resolve().parent
    data = {"title": "MuJoCo 4.0 올리기(#1323) — 전체 점검 통과, 머지 승인 받음", "session": session, "now": "끝 — develop 머지 확인, 브랜치 정리",
            "at": datetime.datetime.now().strftime("%H:%M"), "theme": banner_theme(session)}
    for ms, tag in ((1300, "burst"), (3200, "rest")):
        out = f.with_name(f.stem + f"-{tag}.png")
        r = subprocess.run([sys.executable, str(here / "popup.py"), json.dumps(data, ensure_ascii=False), "--shot", str(out), str(ms)],
                           capture_output=True, text=True, timeout=60)
        print(f"{tag}: {out}" if out.is_file() and r.returncode == 0 else f"{tag}: 실패 {r.stderr.strip()[-200:]}")


def cmd_list(a):
    for s in sorted((summary(t) for t in all_ids()), key=lambda s: (s["status"] == "끝", -s["mine"], -s["mtime"])):
        when = datetime.datetime.fromtimestamp(s["mtime"]).strftime("%m-%d %H:%M")
        print(f"{s['status']:4}  내차례 {s['mine']:<2} {when}  {s['id']:<34} {s['title'][:40]}  [{s['session']}]")


def main(argv=None):
    p = argparse.ArgumentParser(prog="board", description="Task Board — 세션들이 사용자에게 물을 것을 작업별 탭으로")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve")
    n = sub.add_parser("new")
    n.add_argument("task")
    n.add_argument("--title", required=True)
    n.add_argument("--session", required=True, help="ListAgents 맨 위의 이 세션 이름 [ref]")
    n.add_argument("--summary", default="")
    w = sub.add_parser("wait")
    w.add_argument("task", nargs="+")
    w.add_argument("--max", type=float, default=110, help="분 — 백그라운드 작업 상한(2시간) 안으로")
    w.add_argument("--poll", type=float, default=2)
    sub.add_parser("done").add_argument("task")
    sub.add_parser("list")
    sub.add_parser("orphans", help="보냈는데 듣는 세션이 없어 안 읽힌 작업과 깨울 메시지")
    su = sub.add_parser("setup", help="처음 설정 — 설정 파일·명령 링크·PC 화면·채널 MCP(여러 번 해도 같음)")
    su.add_argument("--no-server", action="store_true", help="PC 화면(서버)은 안 켬")
    sub.add_parser("url").add_argument("task", nargs="?", default="")
    bn = sub.add_parser("banner", help="이 세션의 '끝' 배너 꾸미기 — 설정 파일 경로·틀, --shot 이면 PNG 미리보기")
    bn.add_argument("--shot", action="store_true", help="화면에 안 띄우고 PNG 두 장(터질 때·가라앉은 뒤)")
    bn.add_argument("--session", default="", help="세션 이름(기본: 이 명령을 부른 Claude 세션)")
    sub.add_parser("channel-headers", help="(Claude Code 가 부름) board MCP 에 붙을 때 세션 pid 머리글")
    a = p.parse_args(argv)
    if a.cmd == "serve":
        return serve()
    if a.cmd == "url":
        return print(url(a.task))
    try:
        return {"new": cmd_new, "wait": cmd_wait, "done": cmd_done, "list": cmd_list, "orphans": cmd_orphans,
                "setup": cmd_setup, "banner": cmd_banner, "channel-headers": cmd_channel_headers}[a.cmd](a)
    except KeyError as e:
        sys.exit(f"없는 작업: {e} — board list 로 확인")


if __name__ == "__main__":
    sys.exit(main())
