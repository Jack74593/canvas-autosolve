# Canvas LMS MCQ Automated Assist Tool

> TL/DR: Copy and paste this line into Claude Code or your desired coding agent:
```
Clone, and set-up the tool following the instructions in this repository: https://github.com/Jack74593/canvas-autosolve
```
A standalone, pure Python automation assistant and question solver for Canvas LMS assessments.

It connects directly to an active Google Chrome / Chromium browser via the **Chrome DevTools Protocol (CDP)** over WebSockets. **No browser extensions, plugins, or modified binaries are required.**

### Key Capabilities
- **Multi-Tab Quiz Search**: Automatically scans all open browser tabs to discover in-progress and unstarted quizzes.
- **Strict Retake Policy**: Strictly **ignores** any tab or button with "Take the Quiz Again", "Take again", "Retake", or "Làm lại bài kiểm tra".
- **Automatic Quiz Activation**: Automatically starts quizzes on ready tabs by activating "Take the Quiz" / "Làm bài kiểm tra" and waiting for `/take` and questions to render.
- **Full Question Type Coverage**:
  - Single-choice radio buttons (`input[type="radio"]`)
  - Multi-choice checkboxes (`input[type="checkbox"]`)
  - Matching & multiple dropdowns (`select.question_input, select`)
- **Mathematical Formula Engine**: Converts MathML vectors, fractions, roots, super/subscripts, and Canvas equation images into clean LaTeX.
- **Multi-Tab Autonomous Auto Mode**: Solves all questions across all open quiz tabs sequentially, never auto-submitting without student review.
- **Turnkey Agent Support**: Includes dedicated guidance in `AGENTS.md` and `CLAUDE.md` for AI coding agents.

---

## Architecture Overview

```
+-------------------------------------------------------------------+
|                           CANVAS LMS                              |
|   (Live Chrome / Chromium Tab running on --remote-debugging-port)  |
+---------------------------------+---------------------------------+
                                  ^
                                  | WebSocket CDP Protocol (port 9222)
                                  v
+-------------------------------------------------------------------+
|                        canvas_assist.py                           |
|  (Multi-Tab Search, Retake Filter, Auto-Start, MathML, Dropdowns) |
+---------------------------------+---------------------------------+
                                  |
         +------------------------+------------------------+
         |                                                 |
         v                                                 v
+-------------------------------+               +--------------------+
|        AI SOLVER ENGINE       |               | HTTP AGENT BRIDGE  |
| - Local Gateway (Claude/LLM)  |               | (REST API on 8765) |
| - Google Gemini API           |               | - Desktop Agents   |
| - OpenRouter API              |               | - External Bots    |
+-------------------------------+               +--------------------+
```

---

## 1. Turnkey Prompt for Coding Agents (Autonomous Self-Setup)

After cloning the repository and `cd`-ing into it, 

If you are a user, copy and paste this exact prompt into **Claude Code**, Cursor, Windsurf, or any autonomous coding agent. / If you are an AI, read and follow these guidelines:

```markdown
Set up this repository completely on your own from scratch so it is immediately operational. These guidelines apply for macOS, change to fit your current operating platform.

1. Environment & Dependencies:
   - Check if a Python virtual environment (.venv) exists. If not, create one (`python3 -m venv .venv`).
   - Activate it and install dependencies: `pip install -r requirements.txt`.
   - Ensure `canvas_assist.py` is executable (`chmod +x canvas_assist.py`).

2. Browser CDP Connection:
   - Check if Google Chrome is listening on port 9222: `curl -s http://127.0.0.1:9222/json/version`.
   - If port 9222 is NOT responding:
     - Detect the host operating system.
     - Automatically launch Google Chrome in the background with `--remote-debugging-port=9222`:
       * macOS: `/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --remote-debugging-port=9222 --no-first-run --no-default-browser-check >/dev/null 2>&1 &`
       * Linux: `google-chrome --remote-debugging-port=9222 --no-first-run >/dev/null 2>&1 &`
       * Windows: `Start-Process "chrome.exe" -ArgumentList "--remote-debugging-port=9222"`
     - Wait 2 seconds and verify the connection with `curl -s http://127.0.0.1:9222/json/version`.

3. AI Backend Verification:
   - Check for available AI backends: verify if `ANTHROPIC_BASE_URL`, `GEMINI_API_KEY`, or `OPENROUTER_API_KEY` is set in the environment or `.env`.
   - If none are set, check if a local Anthropic proxy is running on `http://localhost:8080`, or prompt me for an API key.

4. Tab & Quiz Discovery:
   - Run `python3 canvas_assist.py search` to scan all open browser tabs.
   - Report the status of all detected tabs (in-progress quizzes, ready-to-start quizzes, and ignored retakes).

5. Readiness:
   - Report when everything is operational, and ask if I would like you to run:
     * Full Auto Mode (Search -> Start -> Solve across tabs): `python3 canvas_assist.py auto`
     * Start All Quizzes Only: `python3 canvas_assist.py start`
     * Status Inspection: `python3 canvas_assist.py status`
```

See **[AGENTS.md](AGENTS.md)** and **[CLAUDE.md](CLAUDE.md)** for architecture details, schemas, and operational guidelines.

---

## 2. Setting Up from a Clean Claude Code Installation

If you just installed Claude Code on a fresh machine:

### Step 1: Open Terminal & Clone Repository
```bash
git clone <repo-url> canvas
cd canvas
```

### Step 2: Set Up Python Environment
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
chmod +x canvas_assist.py
```

### Step 3: Launch Google Chrome with CDP
Ensure Chrome is running with `--remote-debugging-port=9222`:

```bash
# macOS
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --remote-debugging-port=9222 &

# Linux
google-chrome --remote-debugging-port=9222 &

# Windows (Command Prompt)
"C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222
```

Verify that Chrome is listening:
```bash
curl -s http://127.0.0.1:9222/json/version
```

### Step 4: Configure AI Backend Key
Export your API key or local model gateway:

```bash
# Local Claude Code / Anthropic gateway
export ANTHROPIC_BASE_URL="http://localhost:8080"

# Or Google Gemini API key
export GEMINI_API_KEY="your-gemini-key"

# Or OpenRouter API key
export OPENROUTER_API_KEY="your-openrouter-key"
```

### Step 5: Test Connection
```bash
python3 canvas_assist.py search
```

---

## 3. Prerequisites

Before setting up on a new device, ensure you have:

1. **Python 3.10+** (Python 3.10, 3.11, 3.12, or 3.13)
2. **Google Chrome**, **Chromium**, or **Brave** browser
3. **An AI Backend** (at least one of the following):
   - A local model gateway (e.g., Anthropic-compatible local proxy running on `http://localhost:8080`)
   - A Google Gemini API key (`GEMINI_API_KEY`)
   - An OpenRouter API key (`OPENROUTER_API_KEY`)

---

## 2. Installation on a New Device

### Step 1: Clone or Copy the Repository
```bash
git clone <repo-url> canvas-assist
cd canvas-assist
```

### Step 2: Set Up Python Virtual Environment
It is recommended to use a virtual environment:

```bash
# macOS / Linux
python3 -m venv .venv
source .venv/bin/activate

# Windows (Command Prompt)
python -m venv .venv
.venv\Scripts\activate

# Windows (PowerShell)
python -m venv .venv
.venv\Scripts\Activate.ps1
```

### Step 3: Install Required Dependencies
Install the lightweight dependencies via `requirements.txt`:

```bash
pip install -r requirements.txt
```

*(Direct installation if not using requirements.txt: `pip install websockets certifi`)*

Make the script executable (macOS / Linux):
```bash
chmod +x canvas_assist.py
```

---

## 3. Configuring the Browser (Chrome with Remote Debugging)

To allow the Python script to communicate directly with Canvas LMS without any extensions, launch your browser with `--remote-debugging-port=9222`.

### macOS
Close any running instances of Chrome/Chromium, then launch from Terminal:

```bash
# Google Chrome
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --remote-debugging-port=9222

# Chromium
/Applications/Chromium.app/Contents/MacOS/Chromium --remote-debugging-port=9222

# Brave
/Applications/Brave\ Browser.app/Contents/MacOS/Brave\ Browser --remote-debugging-port=9222
```

### Linux
```bash
google-chrome --remote-debugging-port=9222 &
# Or Chromium:
chromium-browser --remote-debugging-port=9222 &
```

### Windows
Close all running Chrome windows, then run in Command Prompt (`cmd.exe`) or PowerShell:

```cmd
"C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222
```
*(If 32-bit Chrome is installed on 64-bit Windows, the path is `C:\Program Files (x86)\Google\Chrome\Application\chrome.exe`)*

### Verify Connection
Once Chrome is open, verify the remote debugging interface is responding:
```bash
curl http://127.0.0.1:9222/json
```
If you see a JSON array of open tabs, the connection is working.

---

## 4. Configuring AI Credentials

Set your preferred AI solver backend via environment variables:

### Option A: Local Model Gateway (Claude Code / Local Proxy)
If you run Claude Code or a local OpenAI/Anthropic gateway on `localhost:8080`:
```bash
export ANTHROPIC_BASE_URL="http://localhost:8080"
```

### Option B: Google Gemini API
```bash
# macOS / Linux
export GEMINI_API_KEY="AIzaSy..."

# Windows (cmd)
set GEMINI_API_KEY=AIzaSy...

# Windows (PowerShell)
$env:GEMINI_API_KEY="AIzaSy..."
```

### Option C: OpenRouter API
```bash
export OPENROUTER_API_KEY="sk-or-v1-..."
```

*(You can also place these in an `.env` file or your shell profile e.g. `~/.zshrc` / `~/.bashrc`)*.

---

## 5. Usage & Command Reference

Navigate to your Canvas quiz page in the browser (e.g., `/courses/:id/quizzes/:quiz_id/take`).

### 1. Check Status
Verify that the tool detects the active quiz tab:
```bash
python3 canvas_assist.py status
```
Output displays:
- Active browser tab title & URL
- Current question title / index
- Available options count
- Navigation status (Next button presence, whether it's the last question)

### 2. Extract Active Question
Inspect the current question and its parsed options in clean JSON format:
```bash
python3 canvas_assist.py extract
```
*Formulas written in MathML or Canvas images are automatically converted to clean LaTeX (e.g. `\vec{v}`, `\omega`, `\sqrt{A^2 - x^2}`).*

### 3. Solve Active Question
Sends the extracted question statement and choices to the AI solver, outputting reasoning and recommended answer:
```bash
python3 canvas_assist.py solve
```

### 4. Answer a Question (Single, Multiple Choice, or Matching Dropdowns)
Select options manually and optionally click Next:

```bash
# Single radio button (by letter or 1-based index)
python3 canvas_assist.py answer B
python3 canvas_assist.py answer 2

# Multiple checkboxes (comma-separated letters or indices)
python3 canvas_assist.py answer "B, C"
python3 canvas_assist.py answer "2, 3"

# Matching Dropdowns (comma-separated answers in order of dropdown items)
python3 canvas_assist.py answer "980 người, 20,50%, Thất nghiệp chu kỳ, Thất nghiệp tự nguyện"

# Select answer and immediately advance to next question
python3 canvas_assist.py answer B --next
```

### 5. Unified Step (Solve, Check/Select Dropdowns, & Next)
Solves the active question (radio, checkbox, or matching dropdowns), selects the appropriate options, and clicks Next in one command:
```bash
python3 canvas_assist.py step
```

### 6. Search All Open Tabs for Quizzes
Scans all open tabs in Google Chrome via CDP, identifies active and startable quizzes, and strictly **ignores tabs with "Take the Quiz Again" / retake buttons**:
```bash
python3 canvas_assist.py search
```

### 7. Automatically Start Quizzes Across All Tabs
Scans all open tabs, ignores "Take the Quiz Again" buttons, and automatically triggers the "Take the Quiz" button on all ready tabs:
```bash
# Start quizzes on all ready tabs
python3 canvas_assist.py start

# Target a specific tab number or ID
python3 canvas_assist.py start --tab 1
```

### 8. Autonomous Multi-Tab Auto Mode (Search, Start, & Solve)
Auto mode automatically scans all open tabs, ignores "Take the Quiz Again" buttons, starts unstarted quizzes, and sequentially solves each quiz until completion:
```bash
# Full auto mode across all open tabs
python3 canvas_assist.py auto

# Auto mode with custom delay between questions
python3 canvas_assist.py auto --delay 1.0

# Only search and start all quizzes without answering
python3 canvas_assist.py auto --start-only

# Start all quizzes upfront before beginning answering
python3 canvas_assist.py auto --start-all

# Target a single tab specifically
python3 canvas_assist.py auto --tab 2
```
- Operates with zero unnecessary delay by default.
- Automatically transitions across all open quiz tabs.
- Safely stops when the final question of each quiz is reached.
- **Never auto-submits the quiz** (`#submit_quiz_button` is protected) to allow review.
- **Strictly ignores retakes**: Skips tabs where button text/attributes match "Take the Quiz Again", "Retake", "Làm lại bài kiểm tra", etc.

---

## 6. Desktop Agent Integration

The tool can be seamlessly driven by external desktop agents (AutoGPT, LangChain, custom Python bots, or Claude Code).

### A. Python Library Import (`CanvasQuizController`)
You can import the high-level controller directly into any Python script:

```python
import asyncio
from canvas_assist import CanvasQuizController

async def run_desktop_agent():
    controller = CanvasQuizController()

    # 1. Search all open tabs for quizzes (ignoring retakes)
    tabs_data = await controller.find_all_quiz_tabs()
    print("Ready quizzes:", len(tabs_data["ready_to_start"]))
    print("Ignored retakes:", len(tabs_data["ignored_retake"]))

    # 2. Automatically start all ready quizzes
    started = await controller.search_and_start_quizzes()
    print("Started quizzes:", started)

    # 3. Extract active question
    q_data = await controller.extract_active_question()
    print("Question:", q_data["text"])
    for opt in q_data["options"]:
        print(f"  {opt['letter']}) {opt['text']} [{opt['type']}]")

    # 4. Your custom desktop agent / reasoning logic determines the answer
    chosen_answers = ["B", "C"]  # or single "A"

    # 5. Select answer and advance immediately
    res = await controller.select_answer(option=chosen_answers, and_next=True, delay=0.0)
    print("Selection confirmed:", res)

if __name__ == "__main__":
    asyncio.run(run_desktop_agent())
```

### B. REST HTTP Bridge Server
Launch the built-in HTTP server to control Canvas over local REST API:

```bash
python3 canvas_assist.py serve --port 8765
```

Endpoints available to any client:

| Method | Endpoint | Description | Payload Example |
| :--- | :--- | :--- | :--- |
| `GET` | `/api/status` | Current quiz state and browser URL | — |
| `GET` | `/api/tabs` | Scan all open tabs for quizzes (ready, taking, ignored retakes) | — |
| `GET` | `/api/question` | Active question JSON, math, & options | — |
| `POST` | `/api/start` | Start all ready quizzes across all tabs (ignores retakes) | — |
| `POST` | `/api/answer` | Check radio/checkbox option(s) | `{"option": "B", "next": true}` |
| `POST` | `/api/solve` | Solves active question via AI | `{"backend": "auto"}` |
| `POST` | `/api/step` | Solve + Answer + Click Next | `{"backend": "auto", "next": true}` |
| `POST` | `/api/next` | Clicks the Next button | — |

Example via `curl`:
```bash
curl -X POST http://127.0.0.1:8765/api/answer \
  -H "Content-Type: application/json" \
  -d '{"option": ["B", "C"], "next": true}'
```

---

## 7. Troubleshooting & FAQ

### 1. `Cannot connect to Chrome on http://127.0.0.1:9222`
- Ensure Google Chrome / Chromium is running.
- Make sure all previous Chrome instances were closed before launching with `--remote-debugging-port=9222`.
- Verify with `curl http://127.0.0.1:9222/json`.

### 2. `No question container found on current page`
- The tab is on the quiz preview / instructions page. You do not need to click manually:
  - Run `python3 canvas_assist.py start` to start all ready quizzes automatically.
  - Or run `python3 canvas_assist.py auto` which will search, start, and solve automatically.
- Tabs with "Take the Quiz Again" / "Làm lại bài kiểm tra" buttons are strictly ignored by design.

### 3. Checkbox vs Radio Button Behavior
- Canvas supports both single-selection radio buttons and multi-selection checkboxes.
- `canvas_assist.py` automatically detects the input type. When multi-selection is required, it checks all matching boxes and unchecks non-selected boxes.

### 4. Matching Dropdowns Support
- Matching and multiple dropdown questions (`select.question_input`) are automatically detected and extracted.
- Choices are matched using exact text, value, normalized decimal values (e.g. `20,50%` $\leftrightarrow$ `20.50%`), substrings, or indices.
- Dispatches native React prototype property descriptor events and jQuery `change` events to trigger autosave.

### 5. Canvas Autosave Synchronization
- Canvas LMS uses background AJAX requests to autosave student answers.
- `canvas_assist.py` fires `pointerdown` → `mousedown` → `pointerup` → `mouseup` → `click` → `input` → `change` events, ensuring Canvas's draft autosave event listeners are triggered.
