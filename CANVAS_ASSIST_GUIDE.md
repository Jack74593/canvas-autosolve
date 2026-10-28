# Canvas LMS MCQ Automated Assist Tool

An automated solving, radio button selection, and next-question navigation assistant for Canvas LMS quizzes.

This system can be controlled directly by:
1. **Claude Code** (running in the terminal / CLI)
2. **Any External Desktop Agent** (via REST HTTP Bridge or Python API)
3. **The Enhanced In-Browser Extension** (with on-screen Floating Control HUD)

---

## 1. Quick Start

### Check Status of Open Quiz
```bash
python3 canvas_assist.py status
```

### Solve Current Question & Advance Automatically
```bash
python3 canvas_assist.py step
```
*What this does in one command:*
1. Extracts active question text, MathML formulas, options, and dropdown/matching items.
2. Solves the question using the AI engine.
3. Automatically selects the correct radio button or sets all dropdown selections with native events.
4. Waits for Canvas draft autosave network sync.
5. Safely clicks the "Next" button.

### Search All Open Tabs for Quizzes
```bash
python3 canvas_assist.py search
```
*Scans all open tabs in Chrome via CDP, identifies active or startable quizzes, and strictly ignores any tabs with "Take the Quiz Again" / retake buttons.*

### Automatically Start All Ready Quizzes
```bash
python3 canvas_assist.py start
```
*Clicks "Take the Quiz" on all ready tabs across Chrome, navigating them to the active quiz interface.*

### Auto-Solve Across All Tabs (Autonomous Pipeline)
```bash
python3 canvas_assist.py auto --delay 1.0
```
*Multi-tab autonomous mode:*
1. Automatically searches for quizzes across all open tabs.
2. Strictly ignores any tabs with "Take the Quiz Again" (retake) buttons.
3. Automatically starts unstarted quizzes.
4. Solves questions sequentially on each quiz tab until all quizzes are finished.
5. Never auto-submits quizzes without confirmation.

---

## 2. Command Line Reference (`canvas_assist.py`)

| Command | Description | Example |
| :--- | :--- | :--- |
| `status` | Checks CDP connection, tab counts, URL, and timer | `python3 canvas_assist.py status` |
| `search` | Searches all open tabs for quizzes & lists their status | `python3 canvas_assist.py search` |
| `start` | Automatically starts all ready quizzes (ignoring retakes) | `python3 canvas_assist.py start` |
| `auto` | Multi-tab auto mode: search, start, and solve all quizzes | `python3 canvas_assist.py auto` |
| `auto --start-only` | Searches all tabs and starts ready quizzes without solving | `python3 canvas_assist.py auto --start-only` |
| `auto --start-all` | Pre-starts all ready quizzes upfront before solving | `python3 canvas_assist.py auto --start-all` |
| `auto --tab <N>` | Targets a single specific tab by number (1..N) or ID | `python3 canvas_assist.py auto --tab 1` |
| `extract` | Extracts active question, LaTeX formulas, and options to JSON | `python3 canvas_assist.py extract` |
| `solve` | Solves current question via AI, outputs reasoning and option | `python3 canvas_assist.py solve` |
| `answer <opt>` | Selects radio button (index 1..N or letter A..D) | `python3 canvas_assist.py answer B` |
| `answer <opt> --next` | Selects radio button, waits delay, and clicks Next | `python3 canvas_assist.py answer 2 --next` |
| `next` | Clicks Next / "Tiếp theo" button safely | `python3 canvas_assist.py next` |
| `step` | Solves, inputs radio button, and advances in one step | `python3 canvas_assist.py step` |
| `serve` | Starts HTTP REST Bridge server for desktop agents | `python3 canvas_assist.py serve --port 8765` |

---

## 3. Binding to Claude Code or Desktop Agents

### A. Terminal / Subagent Execution
An instance of Claude Code or any desktop CLI agent can execute commands directly:
```bash
# 1. Get question data as structured JSON
python3 canvas_assist.py extract > /tmp/current_q.json

# 2. Agent inspects JSON, determines answer (e.g., choice "C")

# 3. Input answer and advance
python3 canvas_assist.py answer C --next --delay 2.0
```

### B. Python API (`CanvasQuizController`)
Any Python script or agent can import the controller directly:
```python
import asyncio
from canvas_assist import CanvasQuizController

async def run_agent():
    ctrl = CanvasQuizController()
    
    # 1. Extract active question
    question = await ctrl.extract_active_question()
    print("Question:", question["text"])
    for opt in question["options"]:
        print(f"  {opt['letter']}) {opt['text']}")
        
    # 2. Agent solves or uses custom reasoning logic
    # ... your desktop agent code here ...
    
    # 3. Select radio button and advance
    result = await ctrl.select_answer(option="B", and_next=True, delay=2.0)
    print("Selection result:", result)

asyncio.run(run_agent())
```

### C. HTTP REST Bridge Server (`http://127.0.0.1:8765`)
Run the bridge server in background or separate terminal:
```bash
python3 canvas_assist.py serve --port 8765
```

Endpoints available to any language / desktop agent:
- `GET /api/status`: Current quiz state, title, and page URL.
- `GET /api/tabs`: Scans all open browser tabs for quizzes (in-progress, ready, ignored retakes).
- `GET /api/question`: Active question statement, MathML LaTeX, and option list.
- `POST /api/start`: Starts all ready quizzes across open tabs (strictly ignores retakes).
- `POST /api/answer`: Selects radio option, checkbox, or matching dropdowns:
  ```json
  { "option": 2, "next": true, "delay": 2.0 }
  ```
  or for matching dropdowns:
  ```json
  { "option": ["980 người", "20,50%", "Thất nghiệp chu kỳ"], "next": true }
  ```
- `POST /api/solve`: Solves active question with AI.
- `POST /api/step`: Solves, answers, and clicks Next.
- `POST /api/next`: Clicks Next button safely.

Example with `curl`:
```bash
# Answer option 2 (B) and click Next
curl -X POST http://127.0.0.1:8765/api/answer \
  -H "Content-Type: application/json" \
  -d '{"option": 2, "next": true, "delay": 2.0}'
```

---

## 4. Multi-Tab Architecture & Dropdown Answering Engine

The pure Python automation engine communicates directly with Chrome via Chrome DevTools Protocol (port 9222).

### A. Multi-Tab Discovery & Retake Filtering
- Scans all open browser tabs via `/json/list`.
- Identifies in-progress quizzes (`[TAKING]`) and quizzes ready to be started (`[READY]`).
- **Strict Retake Filter**: Excludes tabs where button text/attributes match `"Take the Quiz Again"`, `"Take again"`, `"Retake"`, or `"Làm lại bài kiểm tra"`. Retake tabs are categorized as `[IGNORED]` and skipped.

### B. Matching & Multiple Dropdowns Support
- Targets `<select.question_input, select>` within `.question`, `.matching_question`, or New Quizzes containers.
- Matches target choices using exact text, option values, normalized numbers/percentages (handling comma vs dot decimals e.g., `20,50%` $\leftrightarrow$ `20.50%`), substrings, or 1-based indices.
- Dispatches native React prototype descriptor property setters and jQuery `change` events to guarantee Canvas background draft autosave sync.

### C. Turnkey Coding Agent Support
For AI coding agents (Claude Code, Cursor, Windsurf, AutoGPT):
- Refer to **[AGENTS.md](AGENTS.md)** for the turnkey copy-paste agent prompt, schemas, and automation recipes.
- Refer to **[CLAUDE.md](CLAUDE.md)** for workspace rules and guidelines.

---

## 5. Technical Highlights & Safeguards

- **MathML to LaTeX Engine**: Converts `<math>`, `<mfrac>`, `<msqrt>`, `<msup>`, `<msub>`, and `<mover>` (for vector arrows $\vec{v}$) into clean mathematical expressions.
- **Realistic DOM Events**: Dispatches `pointerdown` -> `mousedown` -> `pointerup` -> `mouseup` -> `click`, sets `checked = true`, dispatches `input` & `change` events with `bubbles: true`, and triggers jQuery event handlers.
- **Autosave Safety Delay**: Waits configurable duration (default 2.0s) between answer selection and navigation to ensure Canvas's background AJAX draft sync completes.
- **Anti-Submit Protection**: Next button detectors strictly ignore `#submit_quiz_button` ("Gửi Kiểm Tra" / "Submit Quiz") to prevent accidental early submissions.
