# AGENTS.md — Autonomous Agent Operating Manual

This document provides operational instructions, schemas, architecture details, and setup procedures for AI coding agents (**Claude Code**, Cursor, Windsurf, AutoGPT, LangChain, or custom desktop bots) operating within this repository.

---

## 1. Project Mission & Core Rules

1. **Pure Python Architecture**: The project relies **strictly** on `canvas_assist.py`. The legacy Chrome extension has been discontinued and must not be used.
2. **Chrome DevTools Protocol (CDP)**: The agent communicates with the live Canvas LMS session via WebSockets on `http://127.0.0.1:9222`. No browser extensions or modified browser binaries are required.
3. **Strict Retake Button Policy**:
   - Any button matching "Take the Quiz Again" / "Take quiz again" / "Retake" / "Làm lại bài kiểm tra" / "Thi lại" **MUST BE IGNORED**.
   - The agent must never auto-click retake buttons.
4. **Anti-Auto-Submit Safeguard**:
   - Never auto-click `#submit_quiz_button` ("Submit Quiz" / "Gửi Kiểm Tra").
   - Quizzes must stop at the final question to allow student manual review.

---

## 2. Turnkey Coding Agent Prompt (Copy-Paste Ready)

Copy and paste the exact block below into Claude Code (or any coding agent) immediately after cloning the repository and `cd`-ing into it:

```markdown
You are an autonomous assistant managing the Canvas LMS MCQ Assist Tool in this repository.

Your task is to verify the environment, ensure connectivity to the live browser, inspect all open tabs for Canvas quizzes, and assist in automated quiz solving.

Follow these steps:
1. Environment Verification:
   - Ensure Python 3.10+ is available.
   - Install dependencies: `pip install -r requirements.txt` (requires `websockets>=12.0` and `certifi`).
   - Check if Chrome is running with remote debugging: `curl -s http://127.0.0.1:9222/json/version`
   - If Chrome is not running or not responding on port 9222, instruct the user to launch Chrome with `--remote-debugging-port=9222` or launch it via shell:
     - macOS: `/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --remote-debugging-port=9222 &`
     - Linux: `google-chrome --remote-debugging-port=9222 &`
     - Windows: `"C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222`

2. Check Open Quiz Tabs:
   - Run `python3 canvas_assist.py search` to scan all open browser tabs.
   - Verify active quizzes (`[TAKING]`), ready unstarted quizzes (`[READY]`), and ignored retakes (`[IGNORED]`).

3. Execution Modes:
   - To inspect current quiz status: `python3 canvas_assist.py status`
   - To extract current question data to JSON: `python3 canvas_assist.py extract`
   - To solve and answer current question (radio, checkbox, or matching dropdowns): `python3 canvas_assist.py step`
   - To start all ready quizzes without solving: `python3 canvas_assist.py start`
   - To run the full multi-tab autonomous loop across all quizzes: `python3 canvas_assist.py auto`

4. Safety Constraints:
   - Strictly ignore any "Take the Quiz Again" / "Làm lại bài kiểm tra" buttons.
   - Never auto-submit the quiz form (`#submit_quiz_button` is strictly protected).
   - Support single-choice radio buttons, multi-selection checkboxes, and matching dropdown `<select>` elements.
```

---

## 3. Setup from a Clean Claude Code Installation

When starting in a fresh terminal session with Claude Code:

### Step 1: Clone and Enter the Directory
```bash
git clone <repo-url> canvas
cd canvas
```

### Step 2: Set Up Python Virtual Environment
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
chmod +x canvas_assist.py
```

### Step 3: Launch Google Chrome with CDP
Before running commands, Google Chrome must be running with `--remote-debugging-port=9222`:

```bash
# macOS
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --remote-debugging-port=9222 &

# Linux
google-chrome --remote-debugging-port=9222 &

# Windows (Command Prompt)
"C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222
```

Verify connection:
```bash
curl -s http://127.0.0.1:9222/json | head -n 20
```

### Step 4: Configure AI Backend Credentials
Choose **one** of the following options:

```bash
# Option A: Local Model Gateway (Claude Code proxy / Local server on port 8080)
export ANTHROPIC_BASE_URL="http://localhost:8080"

# Option B: Google Gemini API
export GEMINI_API_KEY="AIzaSy..."

# Option C: OpenRouter API
export OPENROUTER_API_KEY="sk-or-v1-..."
```

*(Optional: Persist these in `.env` or `~/.zshrc` / `~/.bashrc`).*

---

## 4. Agent CLI Command Reference

All operations are executed via `canvas_assist.py`:

| Command | Purpose | Output / Behavior |
| :--- | :--- | :--- |
| `python3 canvas_assist.py search` | Discovers all open tabs | Prints status table (`TAKING`, `READY`, `IGNORED`, `OTHER`) |
| `python3 canvas_assist.py start` | Starts all ready quizzes | Clicks "Take the Quiz", waits for `/take` and questions |
| `python3 canvas_assist.py start --tab <N>` | Starts quiz on tab N | Targets a single specific tab by 1-based index or tab ID |
| `python3 canvas_assist.py auto` | Multi-tab autonomous solver | Searches, starts, and solves all quizzes sequentially |
| `python3 canvas_assist.py auto --delay 1.0` | Auto mode with pacing delay | Waits 1.0s between answer selection and next question |
| `python3 canvas_assist.py auto --start-only` | Starts all quizzes only | Starts ready quizzes without answering questions |
| `python3 canvas_assist.py auto --start-all` | Pre-starts all quizzes upfront | Starts all quizzes first, then solves each sequentially |
| `python3 canvas_assist.py auto --tab <N>` | Targets specific tab in auto | Runs full auto mode on tab N only |
| `python3 canvas_assist.py status` | Tab & question status | Shows tab list and active question details |
| `python3 canvas_assist.py extract` | Extracts question to JSON | MathML $\to$ LaTeX, options, dropdowns, bounding box |
| `python3 canvas_assist.py solve` | Solves current question via AI | Outputs reasoning, option, or dropdown matches |
| `python3 canvas_assist.py answer <opt>` | Selects option manually | Accepts radio (`B`), checkboxes (`"B, C"`), or dropdowns |
| `python3 canvas_assist.py answer <opt> --next` | Answers and advances | Dispatches events, waits delay, clicks Next |
| `python3 canvas_assist.py next` | Clicks Next button safely | Dispatches mouse events on Next, avoids Submit button |
| `python3 canvas_assist.py step` | Solve + Answer + Click Next | Solves active question, sets answers, advances |
| `python3 canvas_assist.py serve --port 8765` | Starts REST HTTP Bridge | Exposes REST API for desktop agents and bots |

---

## 5. Python API Reference (`CanvasQuizController`)

Agents can import and control the quiz directly in Python:

```python
import asyncio
from canvas_assist import CanvasQuizController

async def main():
    ctrl = CanvasQuizController()

    # 1. Discover all open tabs and categorize them
    scan = await ctrl.find_all_quiz_tabs()
    print("In Progress Quizzes:", len(scan["taking"]))
    print("Ready Quizzes (Need Start):", len(scan["ready_to_start"]))
    print("Ignored Retakes (Skipped):", len(scan["ignored_retake"]))

    # 2. Automatically start all ready quizzes (ignoring retakes)
    started_quizzes = await ctrl.search_and_start_quizzes()
    print("Started Quizzes:", started_quizzes)

    # 3. Extract active question
    question = await ctrl.extract_active_question()
    print("Active Question ID:", question["question_id"])
    print("Type:", question["question_type"])

    # 4. Handle Matching / Multiple Dropdowns
    if question.get("is_dropdown"):
        print("Dropdown items count:", len(question["dropdowns"]))
        for dd in question["dropdowns"]:
            print(f"  Item {dd['index']}: '{dd['label']}' -> choices: {dd['available_choices']}")
        
        # Select answers for each dropdown in order:
        res = await ctrl.select_dropdowns(
            dropdown_answers=["Option 1 text", "Option 2 text"],
            and_next=True,
            delay=1.0
        )
        print("Dropdown Selection:", res)

    # 5. Handle Single / Multiple Choice (Radio / Checkbox)
    else:
        res = await ctrl.select_answer(option="B", and_next=True, delay=1.0)
        print("Radio Selection:", res)

if __name__ == "__main__":
    asyncio.run(main())
```

---

## 6. HTTP REST Bridge Server

For agents running in separate processes, Docker, or external languages, start the bridge:

```bash
python3 canvas_assist.py serve --port 8765
```

### Endpoints

| Method | Route | Description | Request Body | Response Format |
| :--- | :--- | :--- | :--- | :--- |
| `GET` | `/api/status` | Current quiz state & URL | None | `{ "page_url": "...", "quiz_state": {...} }` |
| `GET` | `/api/tabs` | Scan all open tabs | None | `{ "total_pages": N, "taking": [...], "ready_to_start": [...], "ignored_retake": [...] }` |
| `GET` | `/api/question` | Active question JSON | None | Complete question JSON with MathML & dropdowns |
| `POST` | `/api/start` | Start all ready quizzes | None | `{ "started": [...] }` |
| `POST` | `/api/answer` | Answer radio/dropdown | `{"option": "B", "next": true}` or `{"option": ["choice1", "choice2"], "next": true}` | `{ "success": true, ... }` |
| `POST` | `/api/solve` | AI solver dispatch | `{"backend": "auto"}` | `{ "reasoning": "...", "selected_options": [...], "dropdown_answers": [...] }` |
| `POST` | `/api/step` | Solve + Answer + Next | `{"backend": "auto", "next": true, "delay": 1.0}` | `{ "solution": {...}, "select": {...} }` |
| `POST` | `/api/next` | Click Next button | None | `{ "success": true, "button_text": "Next" }` |

---

## 7. DOM & Interaction Architecture

### Question Container Anatomy
- Classic Canvas containers: `.question.regular_question`, `.display_question`, `div[id^="question_"]`.
- Canvas New Quizzes containers: `[data-automation="sdk-take-item-question"]`.
- Hidden textarea filters: Excludes `.original_question_text` and `textarea_text_after_answers` to prevent duplicated raw HTML templates.

### MathML to LaTeX Conversion
Automatically normalizes mathematical formulas:
- Vectors: `<mover><mi>v</mi><mo>→</mo></mover>` $\to$ `\vec{v}`
- Fractions: `<mfrac><mrow>a</mrow><mrow>b</mrow></mfrac>` $\to$ `\frac{a}{b}`
- Roots: `<msqrt>x</msqrt>` $\to$ `\sqrt{x}`
- Superscripts/Subscripts: `<msup>`, `<msub>` $\to$ `x^2`, `x_1`
- Canvas Equation Images: `<img class="equation_image" data-math="...">` $\to$ `$math$`

### Dropdown & Matching Question Engine
- Detection: Targets `<select.question_input, select>` within the question.
- Multi-Strategy Matcher:
  1. Exact text match (case-insensitive).
  2. Exact option value match.
  3. Normalized number/percentage match (handles `,` vs `.` decimal separator, e.g. `20,50%` $\leftrightarrow$ `20.50%`, and spacing).
  4. Substring inclusion match (excluding placeholder `[ Choose ]`).
  5. 1-based non-placeholder index match.
- Event Chain:
  Sets value via native prototype setter (`Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value').set`), dispatches `input` and `change` events with `bubbles: true`, and triggers jQuery `change` to guarantee Canvas AJAX draft autosave synchronization.
