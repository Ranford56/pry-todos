#!/usr/bin/env python3
"""PRYs to-do index. MEMORY.md files (source of truth) -> SQLite cache -> web app.

  todos.py sync    scan all MEMORY.md into todos.db (also creates missing ones)
  todos.py init    create missing MEMORY.md stubs only
  todos.py hook    Claude Code Stop hook: auto() if the session ran inside ~/PRYs
  todos.py serve   local web app with direct edits (TODOS_HOST, TODOS_PORT, TODOS_TOKEN)
  todos.py publish pull edits queued from the GitHub Pages site, apply them, push todos.json
                   to the private data repo (TODOS_DATA_REMOTE; cloned into .data/ on first use)
  todos.py auto    publish if .data/ exists, else sync (what the timer and the hook run)
"""
import hashlib, hmac, json, os, re, sqlite3, subprocess, sys, time
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(os.environ.get("PRYS_ROOT", Path.home() / "PRYs")).resolve()
HERE = Path(__file__).resolve().parent
DB = Path(os.environ.get("TODOS_DB", HERE / "todos.db"))
DATA = Path(os.environ.get("TODOS_DATA", HERE / ".data"))
REMOTE = os.environ.get("TODOS_DATA_REMOTE", "git@github.com:Ranford56/pry-todos-data.git")
SKIP = {"node_modules", ".git", ".venv", "venv", "dist", "build", "__pycache__", ".claude"}
LISTS = ("inbox", "next", "waiting", "someday")
STATUS = {" ": "pending", "~": "doing", "x": "done"}
MARK = {v: k for k, v in STATUS.items()}
LINE = re.compile(r"^- \[([ ~xX])\] (.*)$")
TAIL = re.compile(r"(?:\s+#(next|waiting|someday))?(?:\s+✓(\d{4}-\d{2}-\d{2}))?\s*$")
STUB = "# MEMORY.md\n<!-- Gitignored. Maintained by Claude; rules in ~/PRYs/CLAUDE.md -->\n\n## Pending\n\n## Done\n"


def render(mark, text, lst, done):
    return f"- [{mark}] {text}" + (f" #{lst}" if lst != "inbox" else "") + (f" ✓{done}" if done else "")


def parse(line):
    m = LINE.match(line)
    if not m:
        return None
    body = m.group(2)
    t = TAIL.search(body)
    return STATUS[m.group(1).lower()], body[: t.start()], t.group(1) or "inbox", t.group(2)


def project_of(f):
    return "PRYs" if f.parent == ROOT else str(f.parent.relative_to(ROOT))


def walk():
    for d, dirs, names in os.walk(ROOT):
        dirs[:] = sorted(x for x in dirs if x not in SKIP)
        if "MEMORY.md" in names:
            yield Path(d) / "MEMORY.md"


def init():
    """MEMORY.md in every top-level folder and every git repo (any depth). Never overwrites."""
    dirs = {ROOT} | {p for p in ROOT.iterdir() if p.is_dir() and not p.name.startswith(".")}
    for d, subs, names in os.walk(ROOT):
        if ".git" in subs or ".git" in names:  # dir, or file for submodules/worktrees
            dirs.add(Path(d))
        subs[:] = [x for x in subs if x not in SKIP]
    for d in dirs:
        f = d / "MEMORY.md"
        if not f.exists():
            f.write_text(STUB)
        elif not any(LINE.match(x) or x.strip() == "## Pending" for x in f.read_text().splitlines()):
            f.write_text(f.read_text().rstrip("\n") + "\n\n## Pending\n\n## Done\n")  # e.g. pre-existing prose file


def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.executescript(
        "create table if not exists project(name text primary key, path text);"
        "create table if not exists task(id text primary key, project text, path text, line int,"
        " status text, list text, text text, done text);"
    )
    return con


def sync():
    init()
    projects, tasks = [], []
    for f in walk():
        name, seen = project_of(f), {}
        projects.append((name, str(f)))
        for n, line in enumerate(f.read_text().splitlines()):
            p = parse(line)
            if p:
                k = seen[p[1]] = seen.get(p[1], 0) + 1  # same text twice in one file -> distinct ids
                tid = hashlib.sha1(f"{name}\0{p[1]}\0{k}".encode()).hexdigest()[:10]
                tasks.append((tid, name, str(f), n, p[0], p[2], p[1], p[3]))
    with db() as con:
        con.execute("delete from project"); con.execute("delete from task")
        con.executemany("insert into project values (?,?)", projects)
        con.executemany("insert into task values (?,?,?,?,?,?,?,?)", tasks)


def state():
    with db() as con:
        return {
            "projects": [r["name"] for r in con.execute("select name from project order by name")],
            "tasks": [dict(r) for r in con.execute(
                "select id, project, status, list, text, done from task"
                " order by status = 'doing' desc, project, line")],
            "synced": DB.stat().st_mtime,
        }


def _rewrite(path, fn):
    p = Path(path)
    lines = p.read_text().splitlines()
    fn(lines)
    tmp = p.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n")
    tmp.replace(p)


def edit(tid, status=None, lst=None):
    sync()  # line numbers are only trusted right after a scan
    with db() as con:
        r = con.execute("select * from task where id = ?", (tid,)).fetchone()
    if not r:
        raise KeyError(tid)
    status, lst = status or r["status"], lst or r["list"]
    if status not in MARK or lst not in LISTS:
        raise ValueError("bad status/list")
    done = (r["done"] or date.today().isoformat()) if status == "done" else None
    _rewrite(r["path"], lambda L: L.__setitem__(r["line"], render(MARK[status], r["text"], lst, done)))
    sync()


def add(project, text):
    text = " ".join(text.split())[:500]
    if not text:
        raise ValueError("empty text")
    sync()
    with db() as con:
        r = con.execute("select path from project where name = ?", (project,)).fetchone()
    if not r:
        raise KeyError(project)

    def ins(L):
        i = next((i + 1 for i, x in enumerate(L) if x.strip() == "## Pending"), len(L))
        L.insert(i, render(" ", text, "inbox", None))

    _rewrite(r["path"], ins)
    sync()


def git(*args, check=True):
    return subprocess.run(["git", "-C", str(DATA), *args], capture_output=True, text=True, check=check)


def export():
    """Write todos.json into the data repo. `updated` only moves when the content does (no noise commits)."""
    cur = state()
    new = {"projects": cur["projects"], "tasks": cur["tasks"]}
    f = DATA / "todos.json"
    old = json.loads(f.read_text()) if f.exists() else {}
    if all(old.get(k) == v for k, v in new.items()):
        return
    f.write_text(json.dumps({**new, "updated": time.time()}, ensure_ascii=False, indent=1) + "\n")


def publish():
    """Data repo = private GitHub repo. The Pages site reads todos.json and queues edits as actions/*.json;
    this applies them to the MEMORY.md files, re-syncs and pushes the new snapshot. Network errors never abort the local sync."""
    if not DATA.exists():
        subprocess.run(["git", "clone", REMOTE, str(DATA)], check=True, capture_output=True)
    git("pull", "--rebase", "--autostash", check=False)
    for f in sorted((DATA / "actions").glob("*.json")):
        try:  # edit()/add() validate everything; a stale id or bad payload is just dropped
            a = json.loads(f.read_text())
            if a["op"] == "edit":
                edit(a["id"], a.get("status"), a.get("list"))
            elif a["op"] == "add":
                add(a["project"], a["text"])
        except (KeyError, ValueError, TypeError) as e:
            print(f"dropped {f.name}: {e}", file=sys.stderr)
        f.unlink()
    sync()
    export()
    git("add", "-A")
    if git("status", "--porcelain").stdout.strip():
        git("commit", "-m", "sync")
        if git("push", check=False).returncode:  # the site may have queued an action meanwhile
            git("pull", "--rebase", check=False)
            git("push", check=False)


def auto():
    try:
        publish() if DATA.exists() else sync()
    except Exception as e:  # offline, remote missing... the local database must still refresh
        print(f"publish failed: {e}", file=sys.stderr)
        sync()


class Handler(BaseHTTPRequestHandler):
    token = os.environ.get("TODOS_TOKEN", "")

    def send(self, code, data=None, message="", raw=None, ctype="application/json"):
        body = raw if raw is not None else json.dumps(
            {"status_code": code, "status": code < 400, "message": message, "data": data}).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authed(self):
        return not self.token or hmac.compare_digest(self.headers.get("X-Token", ""), self.token)

    def do_GET(self):
        if self.path == "/":
            self.send(200, raw=(HERE / "index.html").read_bytes(), ctype="text/html; charset=utf-8")
        elif self.path == "/api/tasks":
            self.send(200, state(), "Operación exitosa") if self.authed() else self.send(401, message="Unauthorized")
        else:
            self.send(404, message="Not found")

    def do_POST(self):
        # JSON content-type forces a CORS preflight, so other websites can't drive this from a browser
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            return self.send(415, message="JSON only")
        if not self.authed():
            return self.send(401, message="Unauthorized")
        try:
            b = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            if self.path == "/api/refresh":
                sync()
            elif self.path == "/api/tasks":
                add(b["project"], b["text"])
            elif self.path.startswith("/api/tasks/"):
                edit(self.path.rsplit("/", 1)[1], b.get("status"), b.get("list"))
            else:
                return self.send(404, message="Not found")
            self.send(200, state(), "Operación exitosa")
        except (KeyError, ValueError, json.JSONDecodeError) as e:
            self.send(400, message=f"Bad request: {e}")

    def log_message(self, *a):
        pass


def serve():
    host, port = os.environ.get("TODOS_HOST", "127.0.0.1"), int(os.environ.get("TODOS_PORT", 8765))
    if host not in ("127.0.0.1", "localhost", "::1") and not Handler.token:
        sys.exit("Refusing to listen on a network address without TODOS_TOKEN (this app writes files).")
    sync()
    print(f"http://{host}:{port}")
    ThreadingHTTPServer((host, port), Handler).serve_forever()


def hook():
    try:
        cwd = Path(json.load(sys.stdin).get("cwd", "")).resolve()
    except (ValueError, OSError):
        return
    if cwd == ROOT or ROOT in cwd.parents:
        auto()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    {"sync": sync, "init": init, "hook": hook, "serve": serve, "publish": publish, "auto": auto}.get(cmd, lambda: sys.exit(__doc__))()
