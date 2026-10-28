# CLAUDE.md — Project Guidelines for Claude Code

This file is automatically loaded into Claude Code sessions running in this workspace.

---

## 1. Project Overview

- **Core Script**: `canvas_assist.py` (Python 3.10+)
- **Architecture**: Pure Python communicating directly with Google Chrome via **Chrome DevTools Protocol (CDP)** over WebSockets on `http://127.0.0.1:9222`.
- **Extension Status**: The legacy Chrome extension in `CANVAS-AUTO-ANSWER-main/` is **abandoned / deprecated**. Focus **strictly** on the Python script (`canvas_assist.py`).

---

## 2. Core Operational Rules & Safeguards

1. **Strict Retake Button Policy**:
   - Always ignore "Take the Quiz Again" / "Take quiz again" / "Retake" / "Làm lại bài kiểm tra" / "Thi lại" buttons.
   - Tabs with retake buttons must be categorized as `[IGNORED]` and never auto-started.
2. **Anti-Submit Safeguard**:
   - Never auto-click `#submit_quiz_button` ("Submit Quiz" / "Gửi Kiểm Tra").
   - Always stop when the final question is reached to allow student review.
3. **Question Type Support**:
   - Single-choice radio buttons (`input[type="radio"]`)
   - Multi-choice checkboxes (`input[type="checkbox"]`)
   - Matching & multiple dropdowns (`select.question_input, select`)

---

## 3. Essential Commands

### Environment Setup & Verification
```bash
# Check Python syntax
python3 -m py_compile canvas_assist.py

# Verify Chrome CDP is open on port 9222
curl -s http://127.0.0.1:9222/json/version

# Install dependencies
pip install -r requirements.txt
```

### CLI Operations
```bash
# Search all open tabs for quizzes & report status
python3 canvas_assist.py search

# Check active question & quiz status
python3 canvas_assist.py status

# Start all ready quizzes across all open tabs (ignores retakes)
python3 canvas_assist.py start

# Full Multi-Tab Auto Mode (Search -> Start -> Solve across all tabs)
python3 canvas_assist.py auto

# Auto Mode with delay between questions
python3 canvas_assist.py auto --delay 1.0

# Auto Mode: start all quizzes only without answering
python3 canvas_assist.py auto --start-only

# Auto Mode targeting a specific tab
python3 canvas_assist.py auto --tab 1

# Extract active question & options to JSON
python3 canvas_assist.py extract

# Solve active question via AI
python3 canvas_assist.py solve

# Solve, select answer(s), and advance one question
python3 canvas_assist.py step

# Start HTTP REST Bridge for desktop agents
python3 canvas_assist.py serve --port 8765
```

---

## 4. Key Functions in `canvas_assist.py`

- `get_all_page_targets(cdp_host, cdp_port)`: Discovers all open browser pages from CDP.
- `inspect_tab_quiz(tab)`: Analyzes a single tab to determine if it is taking, ready to start, or an ignored retake.
- `find_all_quiz_tabs(cdp_host, cdp_port)`: Gathers inspection results across all tabs in parallel (`asyncio.gather`).
- `start_quiz_on_tab(tab_info, max_wait_sec)`: Clicks "Take the Quiz" via native mouse events and polls until `/take` and questions load.
- `solve_quiz_on_client(client, tab_info, args)`: Manages the Single-Page and Sequential solving loop on a quiz tab.
- `CanvasQuizController`: Public Python API class for external scripts and agents.

---

## 5. Coding & Contribution Conventions

- Keep dependencies minimal (`websockets`, `certifi`).
- When dispatching DOM interactions, always use realistic native mouse/keyboard events (`pointerdown` $\to$ `mousedown` $\to$ `pointerup` $\to$ `mouseup` $\to$ `click` $\to$ `input` $\to$ `change`) and trigger jQuery handlers to ensure Canvas AJAX draft autosave fires.
- For dropdowns, use `Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value').set` for React synthetic event compatibility.
