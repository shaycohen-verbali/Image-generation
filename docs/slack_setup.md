# Slack setup

Connects the deployed backend to a Slack bot so batch status and control are
available from Slack. The backend code already ships the endpoints
(`/api/v1/slack/commands` and `/api/v1/slack/events`); this is configuration.

Commands:

| Command | Does |
|---|---|
| `status` | What is running right now, with progress and last image time |
| `csv [id]` | Job detail; defaults to the running job |
| `health` | App and DB health, running vs waiting job counts |
| `active` | Jobs running or waiting to start |
| `run <id>` | Legacy run status |
| `export <id>` | Export status and download link |
| `start [id]` | Start an imported job; lists candidates if no id given |
| `stop [id]` | Stop the running job |
| `retry [id]` | Requeue failed rows |

The job id is optional everywhere it appears. With one job running the bot uses
it; with several it asks which; with none, read commands fall back to the most
recent job. `start` never guesses, because starting a job spends credits.

**Write commands require a non-empty `SLACK_ALLOWED_USER_IDS`.** Reads are
allowed when it is unset, but `start`, `stop`, and `retry` refuse rather than
let an unset variable expose batch control to the whole workspace.

## 1. Find your backend URL

The manifest needs the Render host that serves the API. In order of
convenience:

1. Open the deployed frontend, DevTools -> Network, click any `/api/v1/...`
   request and read the host.
2. Vercel -> project -> Settings -> Environment Variables -> `VITE_API_BASE`.
   This is the value the frontend actually calls.
3. Render -> the web service -> the URL shown at the top of the page.

## 2. Create the Slack app

Note this is a Slack *app* in your workspace, not the Slack desktop client.
Some workspaces require an admin to approve app installs.

1. Copy `docs/slack_app_manifest.yml` and replace every `RENDER_BACKEND_URL`
   with your host, no trailing slash.
2. https://api.slack.com/apps -> **Create New App** -> **From an app manifest**,
   pick the workspace, paste the YAML.
3. **Install to Workspace** and approve the scopes.

Slack verifies the event request URL when the app is created by POSTing a
challenge. The backend answers that challenge before checking signatures, so it
works before any secret is configured. It does need the Render service awake -
if a cold start times the check out, retry.

## 3. Collect three values

| Value | Where |
|---|---|
| `SLACK_BOT_TOKEN` | app -> OAuth & Permissions -> Bot User OAuth Token (`xoxb-...`) |
| `SLACK_SIGNING_SECRET` | app -> Basic Information -> App Credentials -> Signing Secret |
| `SLACK_ALLOWED_USER_IDS` | your Slack profile -> More -> Copy member ID (`U...`) |

## 4. Set them in Render

Add all three as environment variables on the Render service, then redeploy.

If the worker runs as a **separate** Render service, it needs `SLACK_BOT_TOKEN`
and `SLACK_ALERT_USER_ID` too once push notifications are added. Missing them
there fails silently, at exactly the moment you are relying on an alert.

`SLACK_ALLOWED_USER_IDS` is a comma-separated list. Leaving it empty currently
allows **every** user in the workspace. That is tolerable while the bot is
read-only and must be fixed before write commands ship.

## 5. Verify

```bash
export SLACK_SIGNING_SECRET=...   # same value you set in Render
cd backend
python verify_slack_setup.py https://image-gen-api-mibv.onrender.com --user-id U0123456789
```

Three checks: the event challenge is echoed, a correctly signed slash command is
accepted, and a bad signature is rejected with a 401. The script reads the
secret from your shell and never prints it.

| Failure | Cause |
|---|---|
| event challenge, HTTP 0 | wrong URL, or the Render service is asleep |
| signed command, HTTP 401 | the signing secret in Render differs from your shell |
| `not configured` | `SLACK_BOT_TOKEN` / `SLACK_SIGNING_SECRET` missing on the server |
| `not allowed` | signature verified, but your user ID is not in the allowlist |
| bad signature not rejected | signature checking is not running - do not leave this |

## 6. Try it

In Slack, DM the bot or run `/verbali health`. Both paths go through the same
dispatcher, so if one works the other should too.

## Traps hit during the first setup

**"Your URL didn't respond" on a healthy endpoint.** Slack allows 3 seconds for
the challenge response. A Render cold start alone can take ~2.5s, so creating
the app against a sleeping service fails verification even though the endpoint
is correct. Wake the service with a request first, then press **Retry** next to
the Request URL. It verifies immediately.

**Saving app config silently requires a reinstall.** After changing anything on
the Event Subscriptions page and pressing Save, Slack shows "Please reinstall
your app for these changes to take effect". Until you do, the workspace keeps
the old config and the slash command may not appear in autocomplete. Reinstall
from **Install App -> Reinstall to <workspace>**. Note the bot token can be
reissued by a reinstall, so copy it *after* reinstalling, not before.

**A stale app with a near-identical command.** The Verbali workspace already had
an older app ("Image generation app") owning `/verbali_ig`, created under a
different Slack account so it does not show in this account's app list. Typing
`/verbali` and pressing Enter accepted the `_ig` autocomplete suggestion and
sent the wrong command. Check the app name shown in the autocomplete row, and
remove the stale app via workspace **Settings & administration -> Manage apps**.

**The "Create and Install" dialog can appear to hang.** It had actually
succeeded and only failed to navigate. Reload `api.slack.com/apps` and check
whether the app exists before clicking again, or you will create a duplicate.
