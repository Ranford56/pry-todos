# PRY-TODOS

Personal GTD dashboard over the `MEMORY.md` files of every repo in `~/PRYs`
(Inbox / Next / Waiting / Someday / Done, one project per repo).

```
MEMORY.md files ──todos.py──▶ todos.db ──▶ local app (todos.py serve)
       ▲            │
       │            └─ publish ─▶ PRIVATE repo  pry-todos-data ◀── GitHub API ── this site (GitHub Pages)
       └──── applies actions/*.json queued from the site ───────────────────────┘
```

* The **site is public but contains no data**. Tasks live in a private repo (`todos.json`); the page reads it through the
  GitHub API with a fine-grained token you paste once per device (kept in that browser's localStorage).
* Edits made on the site (complete, move, add) are written to the data repo as `actions/*.json`. `todos.py publish` on your
  PC applies them to the real `MEMORY.md` files, re-syncs and pushes a fresh `todos.json`. A systemd timer runs it every
  5 minutes and the Claude Code Stop hook runs it after each session. Until then the site shows the edit as "⏳ pendiente".
* ⟳ on the site re-reads `todos.json`.

## Setup

1. Private repo `Ranford56/pry-todos-data` with one commit (a README is enough). Another name: set `DATA_REPO` in
   `index.html` and `TODOS_DATA_REMOTE` for `todos.py`.
2. On the PC: `python3 todos.py publish` (clones it to `.data/`, pushes the first `todos.json`).
3. Repo Settings → Pages → Deploy from branch `main`, folder `/ (root)`.
4. Fine-grained token (Settings → Developer settings): only the data repo, permission **Contents: Read and write**.
   Open the site and paste it when asked.
5. Timer: `systemctl --user link ~/PRYs/PRY-TODOS/systemd/pry-todos-sync.{service,timer} && systemctl --user enable --now pry-todos-sync.timer`

## Commands

`todos.py sync | init | serve | publish | auto | hook` (see the docstring). `python3 test_todos.py` runs the checks.
