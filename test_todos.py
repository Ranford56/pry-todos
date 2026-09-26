"""python3 test_todos.py — parse/edit/add round trip on a throwaway tree."""
import io, json, os, sys, tempfile
from pathlib import Path

tmp = Path(tempfile.mkdtemp())
os.environ.update(PRYS_ROOT=str(tmp), TODOS_DB=str(tmp / "t.db"), TODOS_DATA=str(tmp / "data"),
                  TODOS_DATA_REMOTE=str(tmp / "remote.git"), GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                  GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
(tmp / "PRY-A" / "repo" / ".git").mkdir(parents=True)
(tmp / "PRY-A" / "repo" / "node_modules").mkdir()
(tmp / "PRY-A" / "repo" / "node_modules" / "MEMORY.md").write_text("- [ ] must be ignored\n")
(tmp / "PRY-A" / "MEMORY.md").write_text("Prose that stays.\n\n## Pending\n- [~] running #1 now\n- [ ] later #someday\n\n## Done\n- [x] shipped ✓2026-01-02\n")
import todos as t

t.sync()
assert (tmp / "PRY-A" / "repo" / "MEMORY.md").exists() and (tmp / "PRY-B").exists() is False
assert (tmp / "MEMORY.md").exists()
s = t.state()
assert "PRY-A/repo" in s["projects"] and "PRYs" in s["projects"], s["projects"]
by = {x["text"]: x for x in s["tasks"]}
assert set(by) == {"running #1 now", "later", "shipped"}, by.keys()
assert by["later"]["list"] == "someday" and by["shipped"]["done"] == "2026-01-02" and by["running #1 now"]["status"] == "doing"

t.edit(by["later"]["id"], lst="next")
t.edit(by["running #1 now"]["id"], status="done")
t.add("PRY-A", "  new\n thing ")
txt = (tmp / "PRY-A" / "MEMORY.md").read_text()
assert txt.startswith("Prose that stays.") and "## Pending\n- [ ] new thing\n" in txt
assert "- [ ] later #next\n" in txt and "- [x] running #1 now ✓" in txt, txt
t.edit(by["running #1 now"]["id"], status="pending")
assert "- [ ] running #1 now\n" in (tmp / "PRY-A" / "MEMORY.md").read_text()  # date stripped on reopen
for bad in (lambda: t.edit("nope"), lambda: t.add("PRY-A", " "), lambda: t.edit(by["later"]["id"], lst="x")):
    try: bad(); raise SystemExit("expected failure")
    except (KeyError, ValueError): pass

os.remove(t.DB); sys.stdin = io.StringIO(json.dumps({"cwd": "/somewhere/else"})); t.hook()
assert not t.DB.exists()  # hook ignores sessions outside the root
sys.stdin = io.StringIO(json.dumps({"cwd": str(tmp / "PRY-A")})); t.hook()
assert t.DB.exists()

# --- publish: site queues actions in the data repo -> local applies them -> pushes todos.json back
import subprocess
sh = lambda *a, cwd=None: subprocess.run(a, cwd=cwd, check=True, capture_output=True, text=True).stdout
sh("git", "init", "--bare", "-b", "main", str(tmp / "remote.git"))
seed = tmp / "seed"
sh("git", "clone", str(tmp / "remote.git"), str(seed))
(seed / "actions").mkdir()
(seed / "README.md").write_text("data\n")
later = next(x for x in t.state()["tasks"] if x["text"] == "later")
for i, a in enumerate([{"op": "add", "project": "PRY-A", "text": "from the site"},
                       {"op": "edit", "id": later["id"], "status": "done"},
                       {"op": "edit", "id": "gone", "status": "done"},          # stale id: dropped, not fatal
                       {"op": "nuke", "id": "x"}]):                             # unknown op: ignored
    (seed / "actions" / f"{i:03d}.json").write_text(json.dumps(a))
sh("git", "add", "-A", cwd=seed); sh("git", "commit", "-m", "seed", cwd=seed); sh("git", "push", "origin", "HEAD:main", cwd=seed)

t.publish()
txt = (tmp / "PRY-A" / "MEMORY.md").read_text()
assert "- [ ] from the site\n" in txt and "- [x] later #next ✓" in txt, txt
sh("git", "pull", cwd=seed)
pub = json.loads((seed / "todos.json").read_text())
assert {x["text"] for x in pub["tasks"]} >= {"from the site", "later"} and not list((seed / "actions").glob("*.json"))
head = sh("git", "rev-parse", "HEAD", cwd=t.DATA)
t.publish()                                                                     # nothing changed -> no noise commit
assert sh("git", "rev-parse", "HEAD", cwd=t.DATA) == head
print("ok")
