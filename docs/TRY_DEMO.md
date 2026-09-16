# Try the fictional demo

Choose how much setup you want:

| Route | What you can try |
| --- | --- |
| [Open the public demo](https://kajeesan.com/openhealthatlas-demo/) | A clickable, read-only static snapshot with fictional records. No installation or account. |
| Run the local demo below | The real panel, API and local write broker against generated records. |
| [Connect an MCP client](LOCAL_MCP.md) | Query and analyse a fictional database through the actual local MCP server. |

The public snapshot does not run the Python API, save changes, import data or
connect a model. Its displayed values belong to that snapshot, not a fresh
calculation by the current engine. The local demo runs the application; external collectors,
Hermes and Telegram still require their separate integrations.

## Run the local panel

Use macOS or Linux, Git, Python 3.11 or newer and a browser. Native Windows is
not supported by the current Unix-socket broker and analytical file locks.
Dependency installation needs internet access; the fictional demo itself needs
no external account, API key or model.

Run these commands in one terminal. If you already cloned the repository,
start from its root and skip the first two lines.

```bash
git clone https://github.com/kajeesan/Open-Health-Atlas.git
cd Open-Health-Atlas
oha_demo_env="$(mktemp -d)/venv"
python3 -m venv "$oha_demo_env"
"$oha_demo_env/bin/python" -m pip install -r requirements.txt
export PYTHONDONTWRITEBYTECODE=1
unset HERMES_HEVY_QUARTERLY_CONFIG
export HERMES_DEV_DATA_DIR="$(mktemp -d)/openhealthatlas-demo"
export HERMES_DATA_DIR="$HERMES_DEV_DATA_DIR"
export PANEL_DB="$HERMES_DEV_DATA_DIR/panel-dev.db"
export HEALTH_VAULT="$HERMES_DEV_DATA_DIR/vault"
export HERMES_TIMEZONE=UTC
export HERMES_DISPLAY_NAME="Fictional demo"
export BRIDGE_PROFILE=standard
export BRIDGE_HERMESCTL="$HERMES_DEV_DATA_DIR/unconfigured-hermesctl"
"$oha_demo_env/bin/python" scripts/devserver.py
```

Wait for the local server to start, then open
[the demo login](http://localhost:5111/dev-login). This development-only route
signs you in without enrolling a passkey. Do not deploy this script or expose it
to other computers. Run only one instance per operating-system user: the demo
uses fixed local socket names and port 5111.

The first start generates the records and launches the local brokers. Explore
Dashboard, Training, Nutrition, Labs and Body/pain, or add a fictional manual
entry. Health records and panel state stay in the temporary directory printed
by the setup scripts, outside Git. Recovery uses a fixed 2026-06-30 anchor so
the old fictional dates remain meaningful. Other date filters may need to be
set to March–June 2026.

Press `Ctrl-C` in that terminal to stop the panel and its brokers. Restart the
last command in the same terminal to retain your fictional edits. A new
`mktemp` directory gives you a fresh dataset; temporary directories are not
persistent storage for records you want to keep.

## Run the scripted demonstration

After the dependency installation above, from the repository root:

```bash
oha_flow_dir="$(mktemp -d)/openhealthatlas-flow"
"$oha_demo_env/bin/python" scripts/demo_flow.py --data-dir "$oha_flow_dir"
```

This initializes empty databases, imports 45 fictional Google Health-format
days, records ratings through the validated CLI, runs deterministic analysis
and its ledger, and checks authenticated panel/API reads. It prints a JSON
report with `ok: true`, `fictional: true` and `external_services_used: false`
on success. The report is an automated application check, not a browser or
model-answer evaluation.

For code changes, continue with [Development](DEVELOPMENT.md). For a real
service installation with passkeys and the write broker, read
[Deployment](DEPLOYMENT.md).
