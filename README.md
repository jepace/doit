# doit

A lightweight personal task manager. Tasks are stored as plain markdown with inline tags, one file per user.

## Setup

```sh
pip install -r requirements.txt
cp config.json.example config.json
# edit config.json — set base_url, resend_api_key, and generate a secret key
python3 src/serve.py
```

Open `http://127.0.0.1:8080` — register an account on first run.

## Task format

```
- [ ] Task description #p:high #due:2026-05-01 #ctx:work #proj:project-name #rep:1w
  Optional note line
```

Tags: `#p:top/high/medium/low`, `#due:YYYY-MM-DD`, `#ctx:context`, `#proj:name`, `#rep:1d/1w/2w/1m/1y`, `#start:YYYY-MM-DD`, `#star`, `#done:YYYY-MM-DD`

## CLI

```sh
python3 src/tasks.py                  # open tasks sorted by due date / priority
python3 src/tasks.py --due-today      # due today or overdue
python3 src/tasks.py --priority high
python3 src/tasks.py --context work
```
