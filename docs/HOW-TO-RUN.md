# How to run AGENT CRASH LAB: the beginner's guide (Windows, A to Z)

This guide assumes you have never run a Python project before. Follow the steps in order.
Every command goes into **PowerShell**, one line at a time. Press **Enter** after each.

---

## ⚡ The easiest way: double-click `run.bat`

Once Python is installed (section B, step 1), you don't need any of the other steps.
**Double-click `run.bat`** in the project folder. It automatically:

1. finds Python,
2. creates the `.venv` environment the first time,
3. installs the libraries (only the first time, or when they change),
4. starts the server and opens the dashboard in your browser.

To stop it, press **Ctrl + C** in its window, or just close the window.
To use another port, open a terminal in the folder and type `run 8001`.

The rest of this guide explains what `run.bat` does for you, step by step, and how to do more (tests, real AI models, Docker).

---

## A. What this project is, in one minute

| Part | What it is | Where |
|---|---|---|
| **Engine** | The brain. Runs an AI agent inside a fake online shop, breaks things on purpose (timeouts, hacking attempts, race conditions), and checks what really happened. | `crashlab/` |
| **Backend server** | A Python web server (FastAPI) that serves the website, the dashboard and the REST API. | `server/` |
| **Frontend** | The public website and the dashboard. Plain HTML, CSS and JavaScript, so there is **no Node.js / npm needed**. | `web/` |
| **Database** | SQLite: a single file (`crashlab.db`) created automatically. Nothing to install. | created on first run |
| **AI models** | Two built-in test agents (v1 = buggy, v2 = fixed) that need no internet, plus an optional real LLM agent (Claude) that needs an API key. | `crashlab/agents/` |
| **Tests** | 116 automated tests, including security tests and real-browser tests. | `tests/` |

You do **not** need Node.js, PostgreSQL, Redis or Docker to run it. They're optional extras covered at the end.

---

## B. What you need to install (one time only)

### 1. Python (required)

1. Go to **https://www.python.org/downloads/** and download Python **3.12** or newer (3.11 is the minimum).
2. Run the installer. On the first screen, **tick "Add python.exe to PATH"**. This matters.
3. Click **Install Now**.

Check it worked. Open PowerShell (press the Windows key, type `powershell`, press Enter) and run:

```powershell
python --version
```

You should see something like `Python 3.12.x`. If you see "not recognized", re-run the installer and tick "Add to PATH".

### 2. A code editor (recommended, optional)

**Visual Studio Code**: https://code.visualstudio.com. It lets you open the folder, read the code and run a terminal inside it.

### 3. Git (optional; only if you want to put the code on GitHub)

https://git-scm.com/download/win. Accept all defaults.

### 4. Docker Desktop (optional; only for the "production-style" run in section J)

https://www.docker.com/products/docker-desktop. You already have it installed.

---

## C. Open the project folder in PowerShell

The project is at `Desktop\project\agent-crash-lab`. Go there:

```powershell
cd "$HOME\Desktop\project\agent-crash-lab"
```

Check you're in the right place. You should see `README.md`, `crashlab`, `server`, `web` and other files listed:

```powershell
dir
```

> Tip: in VS Code, use **File → Open Folder…**, pick `agent-crash-lab`, then **Terminal → New Terminal**. You'll already be in the right folder.

---

## D. Create a "virtual environment" (one time only)

A virtual environment is a private box for this project's libraries, so they don't mix with anything else on your computer.

```powershell
python -m venv .venv
```

This creates a `.venv` folder. (If you already have a `.venv` folder from earlier, skip this step.)

Now **activate** it:

```powershell
.\.venv\Scripts\Activate.ps1
```

Your prompt now starts with `(.venv)`. **You must activate it every time you open a new PowerShell window** to work on this project.

**If you get a red error saying "running scripts is disabled on this system":** run this once, type `Y` when asked, then activate again:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

---

## E. Install the libraries (one time only)

With `(.venv)` showing, run:

```powershell
python -m pip install --upgrade pip
```

```powershell
pip install -r requirements.lock -e ".[server]"
```

This installs exactly these libraries (pinned versions, checked for known vulnerabilities):

| Library | Why it's needed |
|---|---|
| `fastapi` | The web framework for the backend and REST API |
| `starlette` | What FastAPI is built on (requests, responses, static files) |
| `pydantic` | Validates every incoming API request |
| `uvicorn` | The web server program that runs FastAPI |
| `httpx` | Makes HTTP calls (SDK uploads, Claude API, Cloudflare Turnstile) |
| `jsonschema` | Validates scenario files against the dataset schema |

The `-e .` part installs the project itself and gives you the `crashlab` command.

Check it worked:

```powershell
crashlab --version
```

---

## F. Run it! Three things to try

### 1. The terminal demo (the whole story in 10 seconds)

```powershell
crashlab demo
```

You'll watch it: register an agent → inject a "payment timed out" fault → catch a **duplicate refund** → explain why → shrink the failure to 2 steps → save a regression test → show that the fixed agent passes 10 out of 10 times.

### 2. A full experiment (200 test episodes)

```powershell
crashlab run --agents v1 v2
```

It tests the buggy agent (v1) against the fixed agent (v2) on 20 scenarios × 5 repeats, then prints reliability scores, confidence intervals and every failure mode it found.

### 3. The website and dashboard

```powershell
crashlab serve
```

Leave that window open (the server runs until you stop it), then open these in Chrome or Edge:

| Address | What you'll see |
|---|---|
| http://127.0.0.1:8000 | Public website: landing page, demo video, pricing, FAQ, contact form, privacy policy |
| http://127.0.0.1:8000/signup | Create a user account (email + password, or Google once set up) |
| http://127.0.0.1:8000/login | Sign in |
| http://127.0.0.1:8000/admin/login | Admin sign in → admin console at /admin |
| http://127.0.0.1:8000/app/ | **The dashboard**: start experiments, watch live runs, inspect failures, trace explorer, regression tests |
| http://127.0.0.1:8000/docs | Interactive API documentation (try any endpoint in the browser) |

**First time only: create your admin account.** When the server starts on a new database, the window prints:

```
  FIRST RUN: create your admin account here (one-time link):
  http://127.0.0.1:8000/setup?token=...
```

Hold **Ctrl** and click that link (or copy it into the browser), fill in your name, email and a password of at least 10 characters. You're now the admin. The link stops working once an admin exists.

Then anyone (including you, with a second account) can sign up at `/signup`. Each account gets its own private workspace.

In the dashboard, click **Start an experiment**, then **Start experiment**. You'll see 200 episodes run live, turning green (passed) or red (failed). Then open **Failures**, click a failure, and use **Minimize** and **Save as regression test**.

**To stop the server:** click in the PowerShell window and press **Ctrl + C**.

> Want a quick look without signing in? `crashlab serve --no-auth` turns sign-in off. It only works on your own computer (127.0.0.1), never on a public address.

---

## G. Use a real AI model (Gemini or Claude) instead of the built-in agents (optional)

The built-in agents v1 and v2 are scripted, so they cost nothing and give the same results every time. To test a **real LLM**:

1. Create an API key at **https://console.anthropic.com** (this is a paid service).
2. In your activated PowerShell window, set the key **for this window only**:

```powershell
$env:ANTHROPIC_API_KEY = "paste-your-key-here"
```

3. Run the LLM agent next to the fixed agent:

```powershell
crashlab run --agents v2 llm --repeats 2
```

To choose a different Claude model, set `CRASHLAB_MODEL` the same way before running. The default is `claude-sonnet-5-5`.

**Gemini works the same way.** Get a key at **https://aistudio.google.com/apikey**, then:

```powershell
$env:GEMINI_API_KEY = "paste-your-key-here"
```

```powershell
crashlab run --agents v2 gemini --repeats 2
```

The default model is `gemini-3.8-flash`; set `GEMINI_MODEL` to use another. To keep a key without typing it every time, copy `.env.example` to `.env`, put the key after `GEMINI_API_KEY=`, and start with `run.bat` (it reads `.env`). In the dashboard, the Gemini agent becomes selectable under **New experiment** once the key is set.

**Never** paste your key into any file inside `web/`, and never commit it to GitHub. The key is read only by the Python server, never sent to the browser.

---

## H. Test your own agent (optional)

Open `examples/test_my_agent.py` and read it. It's a short example of a custom agent. Run it:

```powershell
python examples/test_my_agent.py
```

Then change the `my_agent` function to call your own logic, and run it again.

---

## I. Run the automated tests (optional, recommended before any change)

Install the developer tools (one time):

```powershell
pip install -e ".[dev]" ruff playwright
```

```powershell
python -m playwright install chromium
```

Run all 137 tests (about one minute):

```powershell
pytest -q
```

Run the code checker:

```powershell
ruff check crashlab server tests
```

You want to see `116 passed` and `All checks passed!`. **Run these after every change you make (or ask an AI to make).** This is how you catch "fixing one thing broke another".

---

## J. Production-style run with Docker (optional)

This runs it the way a real server would: locked down, sign-in always required.

1. Start **Docker Desktop** and wait until it says it's running.
2. Create your settings file from the template:

```powershell
Copy-Item .env.example .env
```

3. Start it:

```powershell
docker compose up --build
```

4. In the log, find `FIRST RUN: create your admin account here` and open that link to create your admin account. Then open http://127.0.0.1:8000.

To stop it, press **Ctrl + C**. To put it on the internet with HTTPS, follow the comments at the top of `docker-compose.yml` (you need a domain name and a server).

---

## K. Settings reference (the `.env` file)

`.env.example` lists every setting with a comment. The ones you're most likely to touch:

| Setting | What it does |
|---|---|
| `CRASHLAB_AUTH` | `required` = every API call needs a key. Local `crashlab serve` turns this off only on 127.0.0.1. |
| `ANTHROPIC_API_KEY` | Enables the real Claude agent. Server-side only. |
| `CRASHLAB_GA_ID` | Google Analytics. Loads only after a visitor accepts cookies. |
| `TURNSTILE_SITE_KEY` / `TURNSTILE_SECRET_KEY` | Cloudflare spam protection on the contact form |
| `CRASHLAB_CONTACT_EMAIL`, `CRASHLAB_OPERATOR` | Shown in the privacy policy |
| `CRASHLAB_DOMAIN`, `CRASHLAB_PUBLIC_URL` | Your real domain, for HTTPS, sitemap and share previews |

The `.env` file is used by Docker. For `crashlab serve` on your own PC, set a value for one window with `$env:NAME = "value"` as shown in section G.

**`.env` is git-ignored.** Never remove it from `.gitignore`, and never commit real keys.

---

## L. Everyday cheat sheet

Every time you come back to the project:

```powershell
cd "$HOME\Desktop\project\agent-crash-lab"
```

```powershell
.\.venv\Scripts\Activate.ps1
```

```powershell
crashlab serve
```

Other useful commands:

| Command | What it does |
|---|---|
| `crashlab scenarios` | List the 20 test scenarios |
| `crashlab validate` | Check every scenario file is valid |
| `crashlab regress` | Re-run all saved regression tests |
| `crashlab trace <run_id>` | Print one episode step by step |
| `crashlab export-demo --out dist/index.html` | Make a single HTML report file you can send to anyone |
| `crashlab backup create` | Back up the database |
| `crashlab keys create --scopes read,write` | Make an API key (needed when auth is on) |
| `crashlab --help` | Show all commands |

---

## M. Troubleshooting

| Problem | Fix |
|---|---|
| `python` or `pip` "is not recognized" | Reinstall Python with **Add to PATH** ticked, then open a **new** PowerShell window. |
| `crashlab` "is not recognized" | You forgot to activate: `.\.venv\Scripts\Activate.ps1` |
| "running scripts is disabled" | Run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once (section D). |
| "address already in use" / port 8000 busy | Another server is still running. Close it with Ctrl+C, or use another port: `crashlab serve --port 8001` |
| Dashboard sends you to the sign-in page | Sign-in is on by default. Create an account at `/signup`, or create the admin with the setup link (section F.3). |
| Browser shows an old version after you changed a file | Press **Ctrl + F5** to hard-refresh. |
| LLM agent fails with 401 | Your `ANTHROPIC_API_KEY` is wrong or not set in **this** PowerShell window. |
| Want a clean start | Stop the server and delete `crashlab.db` in the project folder. It's recreated on the next run (and prints a new admin setup link). |
| Lost the admin setup link | Stop and start the server again: while no admin exists, it prints a fresh link every start. |
| Forgot a password | An admin opens `/admin` and clicks **Reset link** next to the user. With email set up (`SMTP_*` in `.env`), users can use **Forgot password?** themselves. |
| "Continue with Google" says it didn't complete | The redirect URI in Google Cloud must exactly match the site address + `/auth/google/callback` (section O). |

---

## O. Turn on "Continue with Google" (optional, about 10 minutes)

1. Go to **https://console.cloud.google.com**, sign in, and create a project (any name, e.g. "Agent Crash Lab").
2. Open **APIs & Services → OAuth consent screen**. Choose **External**, fill in the app name and your email, and save. While it is in "Testing", add your own Google address under **Test users**.
3. Open **APIs & Services → Credentials → Create credentials → OAuth client ID**. Application type: **Web application**.
4. Under **Authorized redirect URIs**, add every address the site runs on, ending in `/auth/google/callback`:
   - `http://127.0.0.1:8000/auth/google/callback` (your computer)
   - `https://YOUR-SITE.onrender.com/auth/google/callback` (after deploying, section P)
5. Click **Create**. Copy the **Client ID** and **Client secret**.
6. Put them in `.env` (copy `.env.example` to `.env` first if you haven't):

```
GOOGLE_CLIENT_ID=paste-client-id
GOOGLE_CLIENT_SECRET=paste-client-secret
CRASHLAB_ADMIN_EMAILS=your.name@gmail.com
```

7. Restart with `run.bat`. The sign-in pages now show **Continue with Google**. Signing in with an email listed in `CRASHLAB_ADMIN_EMAILS` makes that account an admin.

---

## P. Put it on the internet with Render

Render runs the app from your GitHub repository and gives it an `https://….onrender.com` address. The repo already contains everything Render needs (`render.yaml` and the `Dockerfile`).

1. **Put the code on GitHub.** Create an empty **private** repository at https://github.com/new, then in the project folder:

```powershell
git remote add origin https://github.com/YOUR-NAME/agent-crash-lab.git
```

```powershell
git push -u origin master
```

   (`.env` and the database are never uploaded: they are in `.gitignore`.)
2. **Create a Render account** at https://render.com (you do this yourself) and connect your GitHub account.
3. In Render: **New → Blueprint**, pick the repository. Render reads `render.yaml` and shows one web service with a 1 GB disk on the **Starter** plan (about $7 a month; the disk keeps accounts and results between deploys).
   *Just want a free demo?* Change `plan: starter` to `plan: free` in `render.yaml` and delete the `disk:` block. Everything works, but the database is wiped on every restart.
4. Render asks for the secret values. Fill in what you have; leave the rest empty:
   `GEMINI_API_KEY`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `CRASHLAB_ADMIN_EMAILS` (your Google email), `ANTHROPIC_API_KEY`, `CRASHLAB_CONTACT_EMAIL`.
5. Click **Apply**. The first build takes a few minutes.
6. **Create the admin:** open the service's **Logs** tab and find the line `FIRST RUN: create your admin account here`. Open that link. (Or simply sign in with Google using the email you put in `CRASHLAB_ADMIN_EMAILS`.)
7. For Google sign-in, add `https://YOUR-SITE.onrender.com/auth/google/callback` to the redirect URIs (section O, step 4).

Every `git push` to GitHub redeploys automatically.

---

## Q. Re-record the demo video or the share image

The landing page's video and the link-preview image are generated from the real app, so they can be refreshed after changes:

```powershell
pip install playwright
```

```powershell
python -m playwright install chromium
```

```powershell
python scripts/record_demo.py
```

```powershell
python scripts/make_og.py
```

`record_demo.py` starts its own server on a throwaway database, signs up a demo user, runs an experiment, and saves `web/media/demo.webm` (about 2 minutes) plus a poster image.

---

## N. Where to read next

1. `README.md`: what the product does and the results it finds
2. `docs/ARCHITECTURE.md`: how the pieces fit together
3. `docs/SECURITY.md`: every "vibe-coded app" failure mode, and the test that proves it's handled
4. The code, in this order: `crashlab/runner.py` → `crashlab/evaluators.py` → `crashlab/diagnosis.py` → `crashlab/minimizer.py`
