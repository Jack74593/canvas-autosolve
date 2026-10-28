# Canvas LMS MCQ Automated Assist Tool

A standalone, Python-based automation assistant and question solver for Canvas LMS assessments.

It connects directly to an active Google Chrome / Chromium browser via the **Chrome DevTools Protocol (CDP)** over WebSockets. **No browser extensions, plugins, or modified binaries are required.**

The tool extracts questions (including MathML formulas and LaTeX), solves them using AI or custom agent logic, clicks the correct radio buttons or checkboxes (with full support for multiple answers), triggers Canvas's native autosave event handlers, and advances through questions automatically.

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
|       (DOM Extraction, MathML Parser, Event Synthesizer)          |
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

## 1. Prerequisites

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
- You may still be on the quiz instructions / preview page. Click **"Take the quiz"** in the browser to start the attempt.
- Ensure the active tab is open to `/courses/:course_id/quizzes/:quiz_id/take`.

### 3. Checkbox vs Radio Button Behavior
- Canvas supports both single-selection radio buttons and multi-selection checkboxes.
- `canvas_assist.py` automatically detects the input type. When multi-selection is required, it checks all matching boxes and unchecks non-selected boxes.

### 4. Canvas Autosave Synchronization
- Canvas LMS uses background AJAX requests to autosave student answers.
- `canvas_assist.py` fires `pointerdown` → `mousedown` → `pointerup` → `mouseup` → `click` → `input` → `change` events, ensuring Canvas's draft autosave event listeners are triggered.
