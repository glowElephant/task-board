#!/usr/bin/env python3
"""board — Task Board(옛 이름 확인판). 여러 Claude 세션이 사용자에게 물을 것을 작업별 문서(탭 하나)로 모은다.

파일이 원본이다: $BOARD_DIR/<작업>/task.md (+ img/). 서버는 그 파일을 보여 주고 사용자의 답·메모·이미지를
그 자리에 고쳐 넣을 뿐 상태를 따로 갖지 않는다. 사용자가 [보내기]를 누르면 <작업>/.sent 가 바뀌고,
그 작업을 `board wait` 로 기다리던 세션이 깨어난다.

  board serve                               화면 서버 (127.0.0.1:BOARD_PORT)
  board new <작업> --title T --session S    작업 문서 만들기
  board wait <작업>... [--max 분]           [보내기]까지 대기 — 세션은 Bash run_in_background 로 건다
                                            exit 0 = 보냄(읽고 반영) · 3 = 시간 초과(다시 건다) · 4 = 사용자 자리 비움(폰 푸시 후 다시 건다)
  board done <작업>                         끝으로 표시 (목록 '끝난 작업' 으로)
  board list                                작업 목록
  board url [<작업>]                        화면 주소

설정: 환경변수 > ~/.config/board/env (KEY=VALUE). BOARD_DIR(기본 ~/board) · BOARD_PORT(기본 8770) ·
BOARD_EXTRA(옛 txt 문서 폴더, os.pathsep 로 여럿 — 탭으로 보여 주기만 한다) ·
BOARD_NOTIFY(0 이면 PC 알림 끔) · BOARD_AWAY_MIN(입력 없이 몇 분이면 자리 비움, 기본 30).
"""
import argparse
import base64
import datetime
import http.server
import json
import mimetypes
import os
import pathlib
import re
import shutil
import socket
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
                vals[k.strip()] = v.strip()
    vals.update({k: v for k, v in os.environ.items() if k.startswith("BOARD_")})
    return vals


CFG = _config()
BOARD = pathlib.Path(CFG.get("BOARD_DIR") or HOME / "board").expanduser()
PORT = int(CFG.get("BOARD_PORT") or 8770)
EXTRA = [pathlib.Path(p).expanduser() for p in (CFG.get("BOARD_EXTRA") or "").split(os.pathsep) if p]
NOTIFY = (CFG.get("BOARD_NOTIFY") or "1") != "0"
AWAY_MIN = float(CFG.get("BOARD_AWAY_MIN") or 30)
WEB = pathlib.Path(__file__).resolve().parent / "web"
HUB = CFG.get("BOARD_HUB", "")              # 목록 아티팩트 주소 — 비면 아티팩트로 안 내보낸다
PC = CFG.get("BOARD_PC") or socket.gethostname()

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
    return {"id": tid, "kind": "task", "title": meta.get("title") or tid, "session": meta.get("session", ""),
            "status": "끝" if meta.get("status", "") in DONE else "진행 중",
            "mine": sum(i["turn"] == "me" for i in items), "waiting": sum(i["turn"] == "claude" for i in items),
            "asks": [f"{i['id']}. {i['title']}" for i in items if i["turn"] == "me"],
            "listening": tid in listeners(), "unread": unread(d), "unpushed": unpushed(tid), "mtime": f.stat().st_mtime}


def all_ids():
    ids = [p.name for p in sorted(BOARD.iterdir()) if (p / "task.md").is_file()] if BOARD.is_dir() else []
    seen = set()
    for d in EXTRA:
        for f in sorted(d.glob("*.txt")) if d.is_dir() else []:
            if f.name not in seen:
                seen.add(f.name)
                ids.append("txt:" + f.name)
    return ids


def detail(tid):
    f, d = resolve(tid)
    text = f.read_text(encoding="utf-8", errors="replace")
    out = summary(tid) | {"raw": text}
    if d is not None:
        meta, body = split(text)
        out["blocks"] = parse(body)
    return out


def send(d, items):
    (d / ".sent").write_text(json.dumps({"at": now(), "items": items}, ensure_ascii=False), encoding="utf-8")


# ── 서버 ─────────────────────────────────────────────────────────────────────

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, body, ctype="application/json; charset=utf-8", code=200):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        try:
            if u.path in ("/", "/index.html"):
                return self.reply((WEB / "index.html").read_bytes(), "text/html; charset=utf-8")
            if u.path == "/api/presence":
                return self.reply(presence())
            if u.path == "/api/tasks":
                return self.reply([summary(t) for t in all_ids()])
            if u.path == "/api/task":
                return self.reply(detail(q["id"]))
            if u.path.startswith("/files/"):
                tid, _, rel = urllib.parse.unquote(u.path[len("/files/"):]).partition("/")
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
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        try:
            if u.path == "/api/presence":
                _seen["beat"] = time.time()
                _seen["at"] = 0.0
                return self.reply(presence())
            if u.path == "/api/image":
                _, d = resolve(q["id"])
                ext = re.sub(r"[^a-z0-9]", "", q.get("ext", "png").lower())[:5] or "png"
                (d / "img").mkdir(exist_ok=True)
                name = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f") + "." + ext
                (d / "img" / name).write_bytes(raw)
                return self.reply({"path": "img/" + name})
            body = json.loads(raw or b"{}")
            tid = body["id"]
            f, d = resolve(tid)
            text = f.read_text(encoding="utf-8", errors="replace")
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


# ── 데스크톱 알림 ────────────────────────────────────────────────────────────

def pending():
    """지금 사용자 차례인 것 — 키가 새로 생기면 알린다(새 질문, 세션이 ⇒ 로 되물음, txt 에 빈 → 사용자 칸)."""
    out = {}
    for tid in all_ids():
        try:
            f, d = resolve(tid)
            if d is None:
                s = summary(tid)
                for i in range(s["mine"]):
                    out[f"{tid}#{i}"] = (tid, s["title"], "txt 문서", "")
                continue
            meta, body = split(f.read_text(encoding="utf-8", errors="replace"))
        except (KeyError, OSError):
            continue
        if meta.get("status", "") in DONE:
            continue
        for b in parse(body):
            if b["type"] == "item" and b["turn"] == "me":
                out[f"{tid}#{b['id']}#{len(b['entries'])}"] = (tid, meta.get("title") or tid, meta.get("session", ""),
                                                             f"{b['id']}. {b['title']}")
    return out


class Popups:
    """GNOME 알림. gi(libnotify)가 있으면 누르면 그 작업 화면이 열린다, 없으면 notify-send 로 글만."""

    def __init__(self):
        self.keep = set()
        try:
            import gi
            gi.require_version("Notify", "0.7")
            from gi.repository import GLib, Notify
            Notify.init("Task Board")
            self.N, self.G = Notify, GLib
            threading.Thread(target=GLib.MainLoop().run, daemon=True).start()
        except (ImportError, ValueError):
            self.N = None

    def show(self, tid, head, body):
        if self.N is None:
            subprocess.run(["notify-send", "-u", "critical", "-a", "Task Board", head, body], check=False)
            return

        def go():
            n = self.N.Notification.new(head, body, "dialog-question")
            n.set_urgency(self.N.Urgency.CRITICAL)   # 방해 금지 중에도 배너로 뜬다(11-03 사용자 Q7 A). 누르거나 닫을 때까지 남는다
            n.add_action("default", "열기", lambda *a: subprocess.Popen(["xdg-open", url(tid)]))
            n.connect("closed", lambda *a: self.keep.discard(n))
            self.keep.add(n)
            n.show()
            return False

        self.G.idle_add(go)


_seen = {"beat": 0.0, "away_since": None, "at": 0.0, "state": "desk"}


def _gdbus(dest, path, method):
    try:
        out = subprocess.run(["gdbus", "call", "--session", "--dest", dest, "--object-path", path, "--method", method],
                             capture_output=True, text=True, timeout=2).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r"\((?:uint64 )?(\w+),\)", out)
    return m.group(1) if m else None


def presence():
    """watching = Task Board 창을 보는 중 · desk = PC 앞(다른 일) · away = AWAY_MIN 분 넘게 키보드·마우스 입력 없음.
    잠금은 따로 안 본다 — 잠그자마자 폰이 울리면 잠깐 자리 뜰 때도 울려서 30분 뒤부터로 했다(11-03 사용자).
    잠겨 있어도 유휴 시간은 계속 쌓인다. GNOME 이 아니면 유휴를 못 읽어 desk 로 본다."""
    t = time.time()
    if t - _seen["at"] < 1.5:
        return {"state": _seen["state"], "away_since": _seen["away_since"]}
    idle = _gdbus("org.gnome.Mutter.IdleMonitor", "/org/gnome/Mutter/IdleMonitor/Core", "org.gnome.Mutter.IdleMonitor.GetIdletime")
    idle_s = int(idle) / 1000 if idle and idle.isdigit() else 0
    if idle_s >= AWAY_MIN * 60:
        state = "away"
    elif t - _seen["beat"] < 12:
        state = "watching"
    else:
        state = "desk"
    if state == "away" and _seen["away_since"] is None:
        _seen["away_since"] = round(t - idle_s)
    elif state != "away":
        _seen["away_since"] = None
    _seen.update(at=t, state=state)
    return {"state": state, "away_since": _seen["away_since"]}


def notify_loop(interval=3):
    popups, prev = Popups(), pending()
    while True:
        time.sleep(interval)
        try:
            cur = pending()
        except Exception:   # 알림이 서버를 죽이면 안 된다 — 다음 바퀴에 다시
            continue
        new = {}
        for k, (tid, title, sess, q) in cur.items():
            if k not in prev:
                new.setdefault(tid, [title, sess, []])[2].append(q)
        prev = cur
        if new and presence()["state"] != "desk":   # 보고 있으면 화면 숫자로 충분, 자리 비움은 세션이 폰으로
            continue
        for tid, (title, sess, qs) in new.items():
            who = re.sub(r"\s*\[[0-9a-f]+\]$", "", sess) or "Task Board"
            popups.show(tid, f"내 차례 · {who}", "\n".join([title] + [q for q in qs if q]))


def serve():
    BOARD.mkdir(parents=True, exist_ok=True)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    if NOTIFY:
        threading.Thread(target=notify_loop, daemon=True).start()
    print(f"Task Board: http://localhost:{PORT}  (문서 {BOARD})", flush=True)
    srv.serve_forever()


# ── 명령 ─────────────────────────────────────────────────────────────────────

TEMPLATE = """---
title: {title}
session: {session}
status: 진행 중
---
## 요약
{summary}

## 정할 것

## 알려 드릴 것

## 메모
"""


def url(tid=""):
    return f"http://localhost:{PORT}/" + (("#" + urllib.parse.quote(tid)) if tid else "")


def cmd_new(a):
    d = BOARD / a.task
    if "/" in a.task or a.task.startswith("."):
        sys.exit("작업 이름에 / 나 맨 앞 . 은 쓸 수 없습니다")
    if (d / "task.md").exists():
        sys.exit(f"이미 있습니다: {d / 'task.md'}")
    d.mkdir(parents=True, exist_ok=True)
    write(d / "task.md", TEMPLATE.format(title=a.title, session=a.session, summary=a.summary or ""))
    print(f"{d / 'task.md'}\n{url(a.task)}")


def report(tid, d):
    info = json.loads((d / ".sent").read_text(encoding="utf-8"))
    print(f"[보냄 {info.get('at', '')}] {tid} — {d / 'task.md'}")
    blocks = parse(split((d / "task.md").read_text(encoding="utf-8"))[1])
    for b in blocks:
        if b["type"] == "item" and b["turn"] == "claude":
            print(f"  {b['id']}. {b['title']}\n    -> " + b["entries"][-1]["text"].replace("\n", "\n    -> "))
        if b["type"] == "memo" and MEMO in info.get("items", []) and b["entries"] and b["entries"][-1]["who"] == "me":
            print("  메모:\n    -> " + b["entries"][-1]["text"].replace("\n", "\n    -> "))
    push = HUB and not local_only(d / "task.md")
    print("→ task.md 를 다시 읽고 질문마다 ⇒ 로 반영(결정된 건 [v]) 한 뒤" + (f" board push {tid} 로 아티팩트에도 올리고," if push else "") + " board wait 를 다시 건다.")


def cmd_wait(a):
    dirs = {}
    for t in a.task:
        _, d = resolve(t)
        if d is None:
            sys.exit(f"txt 문서는 기다릴 수 없습니다: {t}")
        dirs[t] = d
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
        due = away_due(dirs)
        if due:
            print("[자리 비움] 사용자가 자리를 비웠고 내 차례가 남아 있다 — PushNotification(status=proactive) 으로 아래 한 줄을 보내고 board wait 를 다시 건다.")
            print("Task Board · " + " / ".join(f"{title[:40]}: 내 차례 {n}건" for title, n in due))
            return 4
        time.sleep(a.poll)
    print(f"{a.max}분 동안 [보내기] 없음 — board wait {' '.join(a.task)} 를 다시 건다.")
    return 3


def away_due(dirs):
    """자리 비움 동안 작업마다 한 번(내 차례가 늘면 다시) 폰으로 알릴 것. 서버가 꺼져 있으면 알리지 않는다."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/presence", timeout=2) as r:
            p = json.load(r)
    except (OSError, ValueError):
        return []
    if p.get("state") != "away":
        return []
    due = []
    for t, d in dirs.items():
        s = summary(t)
        mark = d / ".pushed"
        try:
            last = json.loads(mark.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            last = {}
        if s["status"] != "끝" and s["mine"] and (last.get("since") != p["away_since"] or s["mine"] > last.get("mine", 0)):
            mark.write_text(json.dumps({"since": p["away_since"], "mine": s["mine"]}), encoding="utf-8")
            due.append((s["title"], s["mine"]))
    return due


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


IMG_ONE, IMG_ALL = 3_000_000, 10_000_000


def session_key(session):
    """세션 아티팩트 키 — 'RBQ 버그픽스 [756bdf]' → '756bdf'(이름이 바뀌어도 ref 는 그대로). ref 가 없으면 이름을 안전한 글자로."""
    m = re.search(r"\[([0-9a-f]+)\]\s*$", session or "")
    return m.group(1) if m else (re.sub(r"[^A-Za-z0-9_-]", "_", session or "unknown")[:60] or "unknown")


def session_tasks(session):
    return [t for t in all_ids() if not t.startswith("txt:") and summary(t)["session"] == session and summary(t)["status"] != "끝"
            and not local_only(resolve(t)[0])]


def page_for(session, also=()):
    """세션 아티팩트 한 장 — PC 화면과 같은 코드(web/index.html)에 그 세션의 진행 중 작업만 심는다(window.BOARD.mode='session').
    세션마다 한 장인 이유(11-04 사용자 "같은 세션끼리만 두면 되잖아"): [보내기]는 아티팩트를 올린 세션만 깨우니 세션 단위면 충분하고,
    작업마다 따로면 아티팩트가 너무 많아지고 처음 보내기 허락도 작업마다 묻는다. 사진은 data: 로(장당 3 MB · 합쳐 10 MB)."""
    ids = list(dict.fromkeys(session_tasks(session) + [t for t in also if t not in session_tasks(session)]))
    tasks, details, images, total = [], {}, {}, 0
    for tid in ids:
        d = detail(tid)
        text = d.pop("raw")
        tasks.append({k: v for k, v in d.items() if k != "blocks"})
        details[tid] = d
        folder = resolve(tid)[1]
        for rel in sorted(set(re.findall(r"!\[[^\]]*\]\((img/[^)\s]+)\)", text))):
            f = folder / rel
            if f.is_file() and f.stat().st_size < IMG_ONE and total + f.stat().st_size < IMG_ALL:
                total += f.stat().st_size
                mime = mimetypes.guess_type(f.name)[0] or "image/png"
                images[f"{tid}/{rel}"] = f"data:{mime};base64," + base64.b64encode(f.read_bytes()).decode()
    data = {"mode": "session", "session": session, "hub": HUB, "pc": PC, "tasks": tasks, "details": details, "images": images, "at": time.time()}
    page = (WEB / "index.html").read_text(encoding="utf-8")
    name = re.sub(r"[<>&]", "", re.sub(r"\s*\[[0-9a-f]+\]$", "", session or ""))[:60]
    page = page.replace("<title>Task Board</title>", f"<title>Task Board · {name}</title>", 1)
    js = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return page.replace("<script>\nconst $ =", "<script>window.BOARD = " + js + ";</script>\n<script>\nconst $ =", 1)


def sess_state(session, upd=None):
    f = BOARD / ".sessions" / (session_key(session) + ".json")
    try:
        st = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    if upd is not None:
        st.update(upd)
        f.parent.mkdir(exist_ok=True)
        write(f, json.dumps(st, ensure_ascii=False))
    return st


def row_id(tid):
    """목록 저장소 문서 id — PC 이름을 붙여 다른 PC 의 같은 작업 이름과 안 겹치게. 허용 글자 밖(한글 등)은 ~xxxx 로."""
    return re.sub(r"[^A-Za-z0-9_\-.]", lambda m: "~%04x" % ord(m.group()), f"{PC}__{tid}")[:200]


def remote(d):
    """작업 폴더의 .remote/ — 아티팩트 주소(url)·목록 줄 버전(version)·올린 시각(at)."""
    try:
        return json.loads((d / ".remote" / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def local_only(f):
    """문서 머리 `local: yes` — 회사 내부 자료(결재 번호·금액·사람 이름 등)가 있어 아티팩트(claude.ai)로 안 보내는 작업.
    11-04 문서업무 세션 요청(사용자 결정 Q10=A: amaranth-docs 는 로컬 전용)."""
    return split(f.read_text(encoding="utf-8", errors="replace"))[0].get("local", "").lower() in ("yes", "true", "1", "예")


def unpushed(tid):
    """문서가 마지막으로 아티팩트에 올린 뒤 바뀌었나(목록 주소가 없거나 로컬 전용이면 늘 False)."""
    if not HUB or tid.startswith("txt:"):
        return False
    f, d = resolve(tid)
    if local_only(f):
        return False
    return f.stat().st_mtime > remote(d).get("at", 0)


def cmd_push(a):
    """세션 아티팩트(그 세션의 진행 중 작업 전부)·목록 줄을 내보낼 준비를 하고, 세션이 부를 도구 호출을 순서대로 찍는다.
    세션이 문서를 고친 그 자리에서 부른다 — 중계 세션 없이 두 곳(로컬 보드·아티팩트)이 같은 문서에서 그려진다(11-04 사용자 결정)."""
    if not HUB:
        sys.exit("BOARD_HUB(목록 아티팩트 주소)가 설정에 없습니다 — ~/.config/board/env")
    f, d = resolve(a.task)
    if d is None:
        sys.exit("txt 문서는 안 됩니다")
    if local_only(f):
        sys.exit(f"{a.task} 는 로컬 전용(local: yes)이라 아티팩트로 안 보냅니다.")
    s = summary(a.task)
    if not s["session"]:
        sys.exit("문서 머리에 session: 이 없습니다 — '이름 [ref]' 를 넣고 다시")
    out = pathlib.Path(a.out).expanduser() if a.out else BOARD / ".sessions"
    out.mkdir(parents=True, exist_ok=True)
    ss, key = sess_state(s["session"]), session_key(s["session"])
    page = out / f"{key}.html"
    write(page, page_for(s["session"], also=[a.task]))   # 방금 끝낸 작업도 이번 한 번은 '끝'으로 보이게
    st = remote(d)
    url = ss.get("url", "")
    row = {"pc": PC, "task": a.task, "title": s["title"], "session": s["session"], "status": s["status"],
           "mine": s["mine"], "waiting": s["waiting"], "asks": s["asks"][:5], "url": (url + "#" + a.task) if url else "", "updated": time.time(),
           "blocks": detail(a.task)["blocks"]}   # 목록이 PC 화면처럼 오른쪽에 내용을 그린다(답은 세션 아티팩트에서)
    (d / ".remote").mkdir(exist_ok=True)
    write(d / ".remote" / "row.json", json.dumps(row, ensure_ascii=False))
    print(f"[push] {a.task} — 세션 아티팩트({s['session']})·목록 줄. 아래 순서대로 부르세요.")
    if url:   # 막 만든 아티팩트는 연결이 붙기 전 온 댓글을 놓칠 수 있다(11-04 실측) — 올릴 때마다 먼저 줍는다
        print(f"0) ArtifactComments read: url={url} — 열린 스레드의 [board …] 중 문서에 아직 없는 것을 board reply 로 먼저 넣고 resolve")
        print(f"1) Artifact publish: file_path={page}  url={url}")
    else:
        print(f'1) Artifact publish: file_path={page}  icon="checklist"  capabilities={{"comments": {{}}, "assets": {{}}}}  (이 세션의 첫 아티팩트)')
        print(f"   → board pushed {a.task} --url <결과 주소>   (세션 아티팩트 주소 기록 + 목록 줄 링크)")
    v = st.get("version")
    print(f"2) ArtifactData set: url={HUB}  collection=tasks  doc_id={row_id(a.task)}  file_path={d / '.remote' / 'row.json'}" + (f"  if_version={v}" if v else "  (처음 — if_version 없이)"))
    print(f"3) board pushed {a.task} --version <결과 version>")
    print("   버전이 안 맞아 거절되면 결과에 나온 현재 version 으로 2) 를 다시. 아티팩트가 없다고 나오면 board pushed <작업> --reset 뒤 다시 push.")


def cmd_hubpage(a):
    """목록 아티팩트 화면 — PC 화면과 같은 코드(web/index.html)를 목록 모드로. 데이터는 심지 않고 db tasks 를 실시간으로 읽는다.
    화면 코드를 고쳤을 때만 다시 올린다(이 PC 의 목록 담당 세션이 BOARD_HUB 주소로 publish, capabilities={"db": {}})."""
    page = (WEB / "index.html").read_text(encoding="utf-8")
    page = page.replace("<script>\nconst $ =", "<script>window.BOARD = " + json.dumps({"mode": "hub", "hub": HUB}) + ";</script>\n<script>\nconst $ =", 1)
    out = pathlib.Path(a.out).expanduser()
    write(out, page)
    print(out)


def cmd_pushed(a):
    f, d = resolve(a.task)
    (d / ".remote").mkdir(exist_ok=True)
    sess = summary(a.task)["session"]
    st = remote(d)
    if a.reset:
        st.pop("version", None)
        sess_state(sess, {"url": ""})
    if a.url:
        sess_state(sess, {"url": a.url})
        rowf = d / ".remote" / "row.json"
        if rowf.is_file():
            row = json.loads(rowf.read_text(encoding="utf-8")); row["url"] = a.url + "#" + a.task
            write(rowf, json.dumps(row, ensure_ascii=False))
    if a.version:
        st["version"] = a.version
    if a.url or a.version:
        st["at"] = time.time()
    write(d / ".remote" / "state.json", json.dumps(st))
    print(f"기록함: {a.task} {st} · 세션 {sess_state(sess)}")


def cmd_reply(a):
    """바깥 화면의 [보내기]·[저장]을 파일에 써 넣는다(중계 세션이 댓글을 받아 부른다). 글 속 ![](로컬 파일) 은 작업 img/ 로 옮긴다.
    담당 세션이 안 듣고 있으면 SendMessage 로 깨울 문구를 찍는다."""
    f, d = resolve(a.task)
    if d is None:
        sys.exit("txt 문서는 안 됩니다")
    answer = pathlib.Path(a.file).read_text(encoding="utf-8") if a.file else sys.stdin.read()

    def move(m):
        src = pathlib.Path(m.group(2)).expanduser()
        if not src.is_absolute() or not src.is_file():
            return m.group(0)
        (d / "img").mkdir(exist_ok=True)
        name = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f") + (src.suffix or ".png")
        shutil.copyfile(src, d / "img" / name)
        return f"![{m.group(1)}](img/{name})"
    answer = re.sub(r"!\[([^\]]*)\]\(([^)\s]+)\)", move, answer)
    text = f.read_text(encoding="utf-8")
    new = add_answer(text, a.item, answer)
    if new == text:
        sys.exit("빈 답 — 아무것도 안 바꿨습니다")
    write(f, new)
    s = summary(a.task)
    if not a.send:
        return print(f"저장함: {a.task} {a.item}")
    send(d, [a.item])
    if a.task in listeners():
        print(f"보냄: {a.task} {a.item} — {s['session']} 가 듣는 중이라 곧 깨어납니다.")
    else:
        print(f"보냄: {a.task} {a.item} — {s['session']} 가 안 듣는 중. SendMessage 로 깨운다:")
        print(f"  Task Board {a.task} 에 사용자가 [보내기] 했는데 이 세션의 board wait 가 꺼져 있습니다."
              f" `board wait {a.task}` 를 Bash run_in_background 로 다시 걸어 주세요 — 바로 끝나며 보낸 내용을 보여 줍니다.")


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
    pu = sub.add_parser("push", help="작업 아티팩트·목록 줄 내보낼 준비 + 부를 도구 호출 안내")
    pu.add_argument("task")
    pu.add_argument("--out", help="만들 파일을 둘 폴더(기본 <작업>/.remote) — Artifact 가 거절하면 세션 scratchpad 로")
    sub.add_parser("hubpage", help="목록 아티팩트 화면 만들기(화면 코드 고쳤을 때만)").add_argument("out")
    pd = sub.add_parser("pushed", help="push 뒤 결과(아티팩트 주소·목록 줄 버전) 기록")
    pd.add_argument("task")
    pd.add_argument("--url")
    pd.add_argument("--version", type=int)
    pd.add_argument("--reset", action="store_true", help="아티팩트가 지워졌을 때 — 주소·버전을 잊는다")
    rp = sub.add_parser("reply", help="바깥 화면에서 온 답을 문서에 넣기")
    rp.add_argument("task")
    rp.add_argument("item", help="Q1 같은 질문 id, 또는 메모")
    rp.add_argument("--file", help="답 글 파일(없으면 표준 입력)")
    rp.add_argument("--save", dest="send", action="store_false", help="보내지 않고 문서에만")
    sub.add_parser("url").add_argument("task", nargs="?", default="")
    a = p.parse_args(argv)
    if a.cmd == "serve":
        return serve()
    if a.cmd == "url":
        return print(url(a.task))
    try:
        return {"new": cmd_new, "wait": cmd_wait, "done": cmd_done, "list": cmd_list, "orphans": cmd_orphans,
                "push": cmd_push, "pushed": cmd_pushed, "hubpage": cmd_hubpage, "reply": cmd_reply}[a.cmd](a)
    except KeyError as e:
        sys.exit(f"없는 작업: {e} — board list 로 확인")


if __name__ == "__main__":
    sys.exit(main())
