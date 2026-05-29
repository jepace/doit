# doit

A lightweight personal task manager. Tasks live in `wiki/tasks.md` as plain markdown with inline tags.

## Setup

```sh
pip install flask
cp config.json.example config.json
python3 src/serve.py
```

Open `http://127.0.0.1:8080` — you'll be prompted to set a password on first run.

## Task format

```
- [ ] Task description #p:high #due:2026-05-01 #ctx:work #proj:project-name
  Optional note line
```

Tags: `#p:top/high/medium/low`, `#due:YYYY-MM-DD`, `#ctx:work`, `#proj:name`, `#rep:1w`, `#start:YYYY-MM-DD`

## CLI

```sh
python3 src/tasks.py                  # open tasks sorted by due date / priority
python3 src/tasks.py --due-today      # due today or overdue
python3 src/tasks.py --priority high
python3 src/tasks.py --context work
```
