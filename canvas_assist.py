#!/usr/bin/env python3
"""
Canvas LMS MCQ Assist Tool
-------------------------
Automated solver, radio button input, and next-question navigator for Canvas LMS quizzes.
Binds to Claude Code, external desktop agents, local LLM gateway, or Gemini/OpenRouter.

Usage:
  python3 canvas_assist.py status                    # Check connection and quiz status
  python3 canvas_assist.py search                    # Search all open tabs for quizzes & report status
  python3 canvas_assist.py start                     # Search all tabs & start unstarted quizzes (ignore retakes)
  python3 canvas_assist.py auto                      # Multi-tab auto mode: search, start, & solve all quizzes
  python3 canvas_assist.py auto --delay 2.5          # Auto-solve loop with delay
  python3 canvas_assist.py auto --start-only         # Only search & start quizzes across all tabs
  python3 canvas_assist.py extract                   # Extract active question & options (JSON)
  python3 canvas_assist.py solve                     # Extract & solve question via AI
  python3 canvas_assist.py answer 2 --next           # Select option 2 (or B) and click Next
  python3 canvas_assist.py next                      # Click Next button safely
  python3 canvas_assist.py step                      # Solve, select radio button, click Next
  python3 canvas_assist.py serve --port 8765         # Start HTTP Agent Bridge for desktop agents
"""

import sys
import os
import json
import time
import re
import argparse
import asyncio
import urllib.request
import urllib.error
import ssl
from typing import Optional, Dict, Any, List

try:
    import websockets
except ImportError:
    print("Error: 'websockets' library is required. Install with: pip install websockets", file=sys.stderr)
    sys.exit(1)

try:
    import certifi
    SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:
    SSL_CTX = ssl.create_default_context()
    SSL_CTX.check_hostname = False
    SSL_CTX.verify_mode = ssl.CERT_NONE

CDP_HOST = os.environ.get("CDP_HOST", "127.0.0.1")
CDP_PORT = int(os.environ.get("CDP_PORT", "9222"))
DEFAULT_GATEWAY_URL = os.environ.get("ANTHROPIC_BASE_URL", "http://localhost:8080")
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")

# ---------------------------------------------------------------------------
# CDP Connection & Browser Helpers
# ---------------------------------------------------------------------------

async def get_all_page_targets(cdp_host: str = CDP_HOST, cdp_port: int = CDP_PORT) -> List[Dict[str, Any]]:
    """Retrieves all open browser page targets from Chrome DevTools Protocol."""
    url = f"http://{cdp_host}:{cdp_port}/json/list"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=4) as resp:
            targets = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        raise RuntimeError(f"Cannot connect to Chrome on http://{cdp_host}:{cdp_port}. Ensure Chrome is running with --remote-debugging-port={cdp_port}. Error: {e}")

    return [t for t in targets if t.get("type") == "page"]


async def get_canvas_page_info(cdp_host: str = CDP_HOST, cdp_port: int = CDP_PORT, target_id: Optional[str] = None) -> Dict[str, Any]:
    """Finds the active Canvas LMS page target from Chrome CDP endpoint."""
    targets = await get_all_page_targets(cdp_host, cdp_port)

    if target_id:
        for t in targets:
            if t.get("id") == target_id:
                return t

    # Prioritize active quiz take page, then general quiz page, then Canvas LMS page
    quiz_take_page = None
    quiz_page = None
    canvas_page = None
    for t in targets:
        target_url = t.get("url", "")
        if "quizzes" in target_url and "take" in target_url:
            quiz_take_page = t
            break
        elif "quizzes" in target_url and not quiz_page:
            quiz_page = t
        elif ("courses" in target_url or "canvas" in target_url or "lms" in target_url) and not canvas_page:
            canvas_page = t

    target = quiz_take_page or quiz_page or canvas_page
    if not target and targets:
        target = targets[0]

    if not target:
        raise RuntimeError("No open browser pages found via Chrome DevTools Protocol.")
    return target


async def get_extension_gemini_key(cdp_host: str = CDP_HOST, cdp_port: int = CDP_PORT) -> Optional[str]:
    """Extracts stored Gemini API key from the local Chromium extension storage if available."""
    try:
        url = f"http://{cdp_host}:{cdp_port}/json/list"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=3) as resp:
            targets = json.loads(resp.read().decode("utf-8"))
        sw = next((t for t in targets if "background.js" in t.get("url", "")), None)
        if not sw:
            return None
        async with websockets.connect(sw["webSocketDebuggerUrl"]) as ws:
            msg = {
                "id": 1,
                "method": "Runtime.evaluate",
                "params": {
                    "expression": "new Promise(r => chrome.storage.local.get(['geminiApiKey', 'openaiApiKey'], r))",
                    "awaitPromise": True,
                    "returnByValue": True
                }
            }
            await ws.send(json.dumps(msg))
            resp = json.loads(await ws.recv())
            val = resp.get("result", {}).get("result", {}).get("value", {})
            return val.get("geminiApiKey") or val.get("openaiApiKey")
    except Exception:
        return None


class CanvasCDPClient:
    """Manages WebSocket communication with the browser tab via CDP."""

    def __init__(self, ws_url: str):
        self.ws_url = ws_url
        self.ws = None
        self._msg_id = 0

    async def __aenter__(self):
        self.ws = await websockets.connect(self.ws_url, max_size=100 * 1024 * 1024)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.ws:
            await self.ws.close()

    async def send_command(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        self._msg_id += 1
        cmd_id = self._msg_id
        payload = {"id": cmd_id, "method": method, "params": params or {}}
        await self.ws.send(json.dumps(payload))
        while True:
            raw = await self.ws.recv()
            data = json.loads(raw)
            if data.get("id") == cmd_id:
                if "error" in data:
                    raise RuntimeError(f"CDP Command '{method}' failed: {data['error']}")
                return data.get("result", {})

    async def eval_js(self, expression: str, await_promise: bool = False, retries: int = 4) -> Any:
        params = {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": await_promise
        }
        for attempt in range(retries):
            try:
                res = await self.send_command("Runtime.evaluate", params)
                res_obj = res.get("result", {})
                if res_obj.get("subtype") == "error":
                    desc = res_obj.get("description", "")
                    if ("context was destroyed" in desc.lower() or "cannot find context" in desc.lower()) and attempt < retries - 1:
                        await asyncio.sleep(0.4)
                        continue
                    raise RuntimeError(f"JavaScript evaluation error: {desc}")
                return res_obj.get("value")
            except Exception as e:
                err_str = str(e).lower()
                if ("context was destroyed" in err_str or "cannot find context" in err_str) and attempt < retries - 1:
                    await asyncio.sleep(0.4)
                    continue
                raise


class CanvasQuizController:
    """High-level API for desktop agents, scripts, and Claude Code instances."""

    def __init__(self, cdp_host: str = CDP_HOST, cdp_port: int = CDP_PORT):
        self.cdp_host = cdp_host
        self.cdp_port = cdp_port

    async def get_page_info(self) -> Dict[str, Any]:
        return await get_canvas_page_info(self.cdp_host, self.cdp_port)

    async def extract_active_question(self) -> Dict[str, Any]:
        page = await self.get_page_info()
        async with CanvasCDPClient(page["webSocketDebuggerUrl"]) as client:
            return await client.eval_js(JS_EXTRACT_QUESTION)

    async def select_answer(self, option: Any, and_next: bool = False, delay: float = 2.0) -> Dict[str, Any]:
        page = await self.get_page_info()
        async with CanvasCDPClient(page["webSocketDebuggerUrl"]) as client:
            expr = f"({JS_SELECT_RADIO})({json.dumps(option)})"
            res = await client.eval_js(expr)
            if and_next and res.get("success"):
                await asyncio.sleep(delay)
                next_res = await client.eval_js(JS_CLICK_NEXT)
                res["next_result"] = next_res
            return res

    async def click_next(self) -> Dict[str, Any]:
        page = await self.get_page_info()
        async with CanvasCDPClient(page["webSocketDebuggerUrl"]) as client:
            return await client.eval_js(JS_CLICK_NEXT)

    async def solve_active_question(self, backend: str = "auto") -> Dict[str, Any]:
        q_data = await self.extract_active_question()
        if not q_data.get("success"):
            return {"success": False, "error": q_data.get("error")}
        solution = await solve_question(q_data, backend=backend)
        return {"success": True, "question": q_data, "solution": solution}

    async def select_dropdowns(self, dropdown_answers: Any, and_next: bool = False, delay: float = 2.0, target_q_id: Optional[str] = None) -> Dict[str, Any]:
        """Selects options in dropdown / matching questions."""
        page = await self.get_page_info()
        async with CanvasCDPClient(page["webSocketDebuggerUrl"]) as client:
            expr = f"({JS_SELECT_DROPDOWNS})({json.dumps(dropdown_answers)}, {json.dumps(target_q_id)})"
            res = await client.eval_js(expr)
            if and_next and res.get("success"):
                await asyncio.sleep(delay)
                next_res = await client.eval_js(JS_CLICK_NEXT)
                res["next_result"] = next_res
            return res

    async def step(self, backend: str = "auto", auto_next: bool = True, delay: float = 2.0) -> Dict[str, Any]:
        sol = await self.solve_active_question(backend=backend)
        if not sol.get("success"):
            return sol
        q_data = sol["question"]
        is_dropdown = q_data.get("is_dropdown", False)
        is_last = q_data.get("is_last_question", False)

        if is_dropdown:
            dd_answers = sol["solution"].get("dropdown_answers") or sol["solution"].get("selected_options") or []
            sel_res = await self.select_dropdowns(
                dropdown_answers=dd_answers,
                and_next=auto_next and not is_last,
                delay=delay,
                target_q_id=q_data.get("question_id")
            )
        else:
            opt = sol["solution"].get("selected_options") or sol["solution"].get("selected_option", 1)
            sel_res = await self.select_answer(
                option=opt,
                and_next=auto_next and not is_last,
                delay=delay
            )
        return {"solution": sol["solution"], "select": sel_res}

    async def find_all_quiz_tabs(self) -> Dict[str, Any]:
        """Discovers all open browser tabs and identifies active or startable quizzes."""
        return await find_all_quiz_tabs(self.cdp_host, self.cdp_port)

    async def start_quiz(self, tab_info: Dict[str, Any], max_wait_sec: float = 15.0) -> Dict[str, Any]:
        """Starts the quiz on the specified tab (ignoring retakes)."""
        return await start_quiz_on_tab(tab_info, max_wait_sec=max_wait_sec)

    async def search_and_start_quizzes(self, max_wait_sec: float = 15.0) -> List[Dict[str, Any]]:
        """Searches all open tabs, ignores 'take again' buttons, and starts all ready quizzes."""
        return await search_and_start_all_quizzes(self.cdp_host, self.cdp_port, max_wait_sec=max_wait_sec)


# ---------------------------------------------------------------------------
# JavaScript Injected Extractors & Actions
# ---------------------------------------------------------------------------

JS_INSPECT_TAB_QUIZ = r"""
(() => {
    const url = window.location.href;
    const title = document.title;

    // Check if currently taking a quiz (questions visible or URL in take mode)
    const hasQuestions = !!(
        document.querySelector("#questions, .question, [data-automation='sdk-take-item-question'], #submit_quiz_form, .quiz_sortable")
    );
    const isTakeUrl = url.includes("/quizzes/") && url.includes("/take");
    const isTaking = hasQuestions || isTakeUrl;

    // Check if this is a Canvas quiz page
    const isQuizPage = url.includes("/quizzes/") || !!document.querySelector("#quiz_details, .quiz-header, #take_quiz_link, .take_quiz_button");

    // Candidate start/retake buttons
    const candidates = Array.from(document.querySelectorAll(
        "#take_quiz_link, .take_quiz_button a, .take_quiz_button button, a.take_quiz_button, a[href*='/take'], button[id*='take'], a.btn-primary, button.btn-primary"
    ));

    // Regex patterns for buttons to IGNORE: "Take the quiz again", retake, làm lại, etc.
    const AGAIN_PATTERNS = [
        /\bagain\b/i,
        /\bretake\b/i,
        /take\s+(the\s+)?quiz\s+again/i,
        /take\s+again/i,
        /làm\s*lại/i,
        /thi\s*lại/i,
        /thực\s*hiện\s*lại/i
    ];

    // Regex patterns for valid quiz start buttons
    const START_PATTERNS = [
        /take\s+(the\s+)?quiz/i,
        /resume\s+(the\s+)?quiz/i,
        /start\s+(the\s+)?quiz/i,
        /\bbegin\b/i,
        /làm\s*bài/i,
        /bắt\s*đầu/i,
        /tiếp\s*tục/i,
        /thực\s*hiện/i
    ];

    let validStartBtn = null;
    let ignoredAgainBtn = null;

    for (const el of candidates) {
        if (el.offsetParent === null) continue; // Skip hidden elements
        const text = (el.innerText || el.value || el.getAttribute("aria-label") || "").trim();
        const id = el.id || "";
        const href = el.getAttribute("href") || el.href || "";
        const cls = el.className || "";

        if (el.disabled || el.getAttribute("aria-disabled") === "true" || el.classList.contains("disabled")) {
            continue;
        }

        // Exclude quiz submit buttons
        if (id === "submit_quiz_button" || /submit|gửi\s*kiểm\s*tra/i.test(text)) {
            continue;
        }

        const combined = `${text} ${id} ${cls} ${href}`;

        // Check for 'Take again' / 'retake'
        const isAgain = AGAIN_PATTERNS.some(p => p.test(combined));
        if (isAgain) {
            ignoredAgainBtn = {
                text: text,
                id: id,
                href: href,
                selector: id ? `#${id}` : (cls ? `.${cls.trim().split(/\s+/).join('.')}` : 'a')
            };
            continue;
        }

        // Check for valid start
        const isStart = id === "take_quiz_link" || href.includes("/take") || START_PATTERNS.some(p => p.test(text));
        if (isStart && !validStartBtn) {
            validStartBtn = {
                text: text,
                id: id,
                href: href,
                tag: el.tagName,
                selector: id ? `#${id}` : (cls ? `.${cls.trim().split(/\s+/).join('.')}` : 'a#take_quiz_link')
            };
        }
    }

    const timeLimitEl = document.querySelector(".time_limit, .quiz_time_limit");
    const allowedAttemptsEl = document.querySelector(".allowed_attempts");

    return {
        url: url,
        title: title,
        is_quiz_page: isQuizPage,
        is_taking: isTaking,
        can_start: !isTaking && !!validStartBtn,
        start_button: validStartBtn,
        ignored_again_button: ignoredAgainBtn,
        time_limit: timeLimitEl ? timeLimitEl.innerText.trim() : null,
        allowed_attempts: allowedAttemptsEl ? allowedAttemptsEl.innerText.trim() : null
    };
})()
"""

JS_START_QUIZ = r"""
(() => {
    const AGAIN_PATTERNS = [
        /\bagain\b/i,
        /\bretake\b/i,
        /take\s+(the\s+)?quiz\s+again/i,
        /take\s+again/i,
        /làm\s*lại/i,
        /thi\s*lại/i,
        /thực\s*hiện\s*lại/i
    ];
    const START_PATTERNS = [
        /take\s+(the\s+)?quiz/i,
        /resume\s+(the\s+)?quiz/i,
        /start\s+(the\s+)?quiz/i,
        /\bbegin\b/i,
        /làm\s*bài/i,
        /bắt\s*đầu/i,
        /tiếp\s*tục/i,
        /thực\s*hiện/i
    ];

    const candidates = Array.from(document.querySelectorAll(
        "#take_quiz_link, .take_quiz_button a, .take_quiz_button button, a.take_quiz_button, a[href*='/take'], button[id*='take'], a.btn-primary, button.btn-primary"
    ));

    let btn = null;
    let ignoredAgain = null;

    for (const el of candidates) {
        if (el.offsetParent === null) continue;
        const text = (el.innerText || el.value || el.getAttribute("aria-label") || "").trim();
        const id = el.id || "";
        const href = el.getAttribute("href") || el.href || "";
        const cls = el.className || "";

        if (el.disabled || el.getAttribute("aria-disabled") === "true" || el.classList.contains("disabled")) {
            continue;
        }
        if (id === "submit_quiz_button" || /submit|gửi\s*kiểm\s*tra/i.test(text)) {
            continue;
        }

        const combined = `${text} ${id} ${cls} ${href}`;
        if (AGAIN_PATTERNS.some(p => p.test(combined))) {
            ignoredAgain = text;
            continue; // STRICTLY IGNORE "take the quiz again" buttons!
        }

        const isStart = id === "take_quiz_link" || href.includes("/take") || START_PATTERNS.some(p => p.test(text));
        if (isStart) {
            btn = el;
            break;
        }
    }

    if (!btn) {
        if (ignoredAgain) {
            return { success: false, error: `Only 'take again' button found ('${ignoredAgain}') - ignored.` };
        }
        return { success: false, error: "No valid quiz start button found on page." };
    }

    // Scroll into view & click
    try {
        btn.scrollIntoView({ behavior: 'smooth', block: 'center' });
    } catch (_) {}

    const rect = btn.getBoundingClientRect();
    const cx = Math.floor(rect.left + rect.width / 2);
    const cy = Math.floor(rect.top + rect.height / 2);

    ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click'].forEach(evtName => {
        const isDown = evtName.includes('down');
        btn.dispatchEvent(new MouseEvent(evtName, {
            view: window,
            bubbles: true,
            cancelable: true,
            clientX: cx,
            clientY: cy,
            button: 0,
            buttons: isDown ? 1 : 0
        }));
    });

    btn.click();
    if (window.jQuery) {
        try {
            window.jQuery(btn).trigger('click');
        } catch (_) {}
    }

    return {
        success: true,
        button_text: (btn.innerText || btn.value || "").trim(),
        href: btn.getAttribute("href") || btn.href || ""
    };
})()
"""

JS_CHECK_QUIZ_READY = r"""
(() => {
    const url = window.location.href;
    const hasQuestions = !!document.querySelector("#questions, .question, .question_holder, [data-automation='sdk-take-item-question'], #submit_quiz_form, .quiz_sortable");
    const isTakeUrl = url.includes("/quizzes/") && url.includes("/take");
    const qCount = document.querySelectorAll(".question, .question_holder, .display_question, div[id^='question_']").length;
    return {
        url: url,
        isTakeUrl: isTakeUrl,
        hasQuestions: hasQuestions,
        questionCount: qCount,
        ready: hasQuestions || isTakeUrl
    };
})()
"""

JS_EXTRACT_QUESTION_FN = r"""
((targetQId = null) => {
    // MathML to LaTeX / readable text converter
    function mathToText(mathEl) {
        if (!mathEl) return '';
        // Check for annotation with LaTeX
        const tex = mathEl.querySelector('annotation[encoding="application/x-tex"], annotation[encoding="LaTeX"]');
        if (tex && tex.textContent.trim()) {
            return '$' + tex.textContent.trim() + '$';
        }
        if (mathEl.getAttribute('alttext')) {
            return '$' + mathEl.getAttribute('alttext').trim() + '$';
        }

        // Clone so we can replace tags with readable math
        const clone = mathEl.cloneNode(true);

        // Vector notation: <mover><mi>v</mi><mo>→</mo></mover> -> \vec{v}
        clone.querySelectorAll('mover').forEach(m => {
            const mi = m.querySelector('mi');
            const mo = m.querySelector('mo');
            const moText = mo ? mo.textContent.trim() : '';
            if (moText === '→' || moText === '->' || moText === '&#8594;' || moText === '→') {
                m.textContent = `\\vec{${mi ? mi.textContent.trim() : 'v'}}`;
            }
        });

        // Fractions: <mfrac><mrow>num</mrow><mrow>den</mrow></mfrac> -> \frac{num}{den}
        clone.querySelectorAll('mfrac').forEach(f => {
            const children = Array.from(f.children);
            if (children.length >= 2) {
                f.textContent = `\\frac{${children[0].textContent.trim()}}{${children[1].textContent.trim()}}`;
            }
        });

        // Roots: <msqrt>...</msqrt> -> \sqrt{...}
        clone.querySelectorAll('msqrt').forEach(r => {
            r.textContent = `\\sqrt{${r.textContent.trim()}}`;
        });

        // Superscripts: x^2
        clone.querySelectorAll('msup').forEach(s => {
            const children = Array.from(s.children);
            if (children.length >= 2) {
                s.textContent = `${children[0].textContent.trim()}^{${children[1].textContent.trim()}}`;
            }
        });

        // Subscripts: x_1
        clone.querySelectorAll('msub').forEach(s => {
            const children = Array.from(s.children);
            if (children.length >= 2) {
                s.textContent = `${children[0].textContent.trim()}_{${children[1].textContent.trim()}}`;
            }
        });

        return ' ' + clone.textContent.replace(/\s+/g, ' ').trim() + ' ';
    }

    // Extract text with math replacement
    function getCleanContent(rootEl, excludeAnswers = false) {
        if (!rootEl) return '';
        const clone = rootEl.cloneNode(true);
        // Remove existing assist / cream buttons or extraneous elements
        let toRemove = '.cream-btn, .screen-reader-only, .screenreader-only, .original_question_text, textarea, .flag_question, script, style';
        if (excludeAnswers) toRemove += ', .answers';
        clone.querySelectorAll(toRemove).forEach(el => el.remove());

        // Convert MathML in clone
        clone.querySelectorAll('math').forEach(m => {
            const mathText = mathToText(m);
            const span = document.createElement('span');
            span.textContent = mathText;
            m.replaceWith(span);
        });

        // Canvas math equation images: <img class="equation_image" data-math="..." alt="..." />
        clone.querySelectorAll('img.equation_image, img[data-math]').forEach(img => {
            const math = img.getAttribute('data-math') || img.getAttribute('alt') || '';
            if (math) {
                const span = document.createElement('span');
                span.textContent = ` $${math}$ `;
                img.replaceWith(span);
            }
        });

        return clone.innerText.replace(/\r\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim();
    }

    // Find all true top-level question containers on page
    const allQuestions = Array.from(document.querySelectorAll('.question.regular_question, .display_question, div[id^="question_"]')).filter(q => {
        if (q.offsetParent === null) return false;
        if (q.classList.contains('question_summary')) return false;
        if (q.id && (q.id.includes('_text') || q.id.includes('_label') || q.id.includes('_answer'))) return false;
        if (!q.querySelector('.question_text, .text, input[type="radio"], input[type="checkbox"], select')) return false;
        if (q.parentElement && q.parentElement.closest('.question, [id^="question_"]')) return false;
        return true;
    });
    const isSinglePage = allQuestions.length > 1;

    let container = null;
    let isSingleQuestionMode = false;

    if (targetQId) {
        container = document.getElementById(targetQId);
    }

    // Check for Canvas New Quizzes
    if (!container) {
        const nq = document.querySelector('[data-automation="sdk-take-item-question"]');
        if (nq) {
            container = nq;
            isSingleQuestionMode = true;
        }
    }

    // Check for Classic Canvas quiz containers
    if (!container) {
        if (allQuestions.length === 1) {
            container = allQuestions[0];
            isSingleQuestionMode = true;
        } else if (allQuestions.length > 1) {
            // Find first unanswered question on page
            let firstUnanswered = null;
            for (const q of allQuestions) {
                const isAnswered = q.classList.contains('answered') ||
                    Array.from(q.querySelectorAll('input[type="radio"], input[type="checkbox"]')).some(i => i.checked) ||
                    (q.querySelectorAll('select').length > 0 && Array.from(q.querySelectorAll('select')).every(s => s.value !== ''));
                if (!isAnswered) {
                    firstUnanswered = q;
                    break;
                }
            }
            container = firstUnanswered || allQuestions[0];
        }
    }

    if (!container) {
        // Fallback: search for any question_holder
        container = document.querySelector('.question_holder, #questions .question');
    }

    if (!container) {
        return {
            error: "No question container found on current page.",
            url: window.location.href,
            title: document.title
        };
    }

    // Question header & ID
    const qId = container.id || container.getAttribute('data-question-id') || 'active_question';
    const headerEl = container.querySelector('.header, .question_name, .header .name, h1, h2, h3');
    const qHeader = headerEl ? headerEl.innerText.trim() : '';

    // Question body text
    const textEl = container.querySelector('.question_text.user_content, .text .user_content, .question_text, .text, [data-automation="sdk-item-wrapper"]');
    const questionText = getCleanContent(textEl || container, true);

    // Images
    const images = [];
    (textEl || container).querySelectorAll('img:not(.equation_image)').forEach(img => {
        let src = img.getAttribute('src') || '';
        if (src && !src.startsWith('data:') && !src.includes('icon')) {
            try {
                src = new URL(src, window.location.href).href;
            } catch (_) {}
            images.push({
                src: src,
                alt: img.getAttribute('alt') || '',
                width: img.naturalWidth || img.width || 0,
                height: img.naturalHeight || img.height || 0
            });
        }
    });

    // Options (Radio buttons & Checkboxes)
    const options = [];
    const radioInputs = Array.from(container.querySelectorAll('input[type="radio"], input[type="checkbox"]'));
    const isCheckboxQuestion = radioInputs.some(i => i.type === 'checkbox');

    radioInputs.forEach((input, idx) => {
        let labelText = '';
        // 1. Associated label via 'for'
        if (input.id) {
            const label = container.querySelector(`label[for="${input.id}"]`);
            if (label) {
                const answerTextEl = label.querySelector('.answer_text') || label;
                labelText = getCleanContent(answerTextEl);
            }
        }
        // 2. Parent label
        if (!labelText) {
            const parentLabel = input.closest('label');
            if (parentLabel) {
                labelText = getCleanContent(parentLabel);
            }
        }
        // 3. Parent .answer container
        if (!labelText) {
            const answerDiv = input.closest('.answer, .answer_label');
            if (answerDiv) {
                const textEl = answerDiv.querySelector('.answer_text') || answerDiv;
                labelText = getCleanContent(textEl);
            }
        }

        const letters = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H'];
        const letter = letters[idx] || `${idx + 1}`;

        options.push({
            index: idx + 1,
            letter: letter,
            id: input.id || '',
            name: input.name || '',
            value: input.value || '',
            type: input.type,
            text: labelText.trim(),
            checked: input.checked
        });
    });

    // Dropdowns (Select elements for Matching / Multiple Dropdown questions)
    const selectEls = Array.from(container.querySelectorAll('select.question_input, select'));
    const isDropdownQuestion = selectEls.length > 0;
    const dropdowns = [];

    selectEls.forEach((sel, selIdx) => {
        let labelText = '';
        if (sel.id) {
            const lbl = container.querySelector(`label[for="${sel.id}"]`);
            if (lbl) labelText = getCleanContent(lbl);
        }
        if (!labelText) {
            const parentRow = sel.closest('.answer, tr, .matching_question_answer');
            if (parentRow) {
                const rowLbl = parentRow.querySelector('label, .answer_label, td:first-child');
                if (rowLbl) labelText = getCleanContent(rowLbl);
            }
        }
        if (!labelText) {
            labelText = `Item ${selIdx + 1}`;
        }

        const opts = [];
        Array.from(sel.options).forEach((opt, optIdx) => {
            const optText = opt.textContent.trim();
            const isPlaceholder = opt.value === '' || /^\[?\s*(choose|chọn|select)\s*\]?$/i.test(optText);
            opts.push({
                index: optIdx,
                value: opt.value,
                text: optText,
                is_placeholder: isPlaceholder,
                selected: opt.selected
            });
        });

        dropdowns.push({
            index: selIdx + 1,
            id: sel.id || '',
            name: sel.name || '',
            label: labelText.trim(),
            options: opts,
            available_choices: opts.filter(o => !o.is_placeholder).map(o => o.text),
            current_value: sel.value,
            current_text: sel.options[sel.selectedIndex] ? sel.options[sel.selectedIndex].text.trim() : ''
        });
    });

    // Jump List & Total Questions Computation
    const jumpLinks = Array.from(document.querySelectorAll('#right-side .question_list li a, #quiz_question_list li a, .question_nav a'));
    const jumpLis = Array.from(document.querySelectorAll('#right-side .question_list li, #quiz_question_list li'));

    let totalQuestions = jumpLinks.length || allQuestions.length || 1;
    let currentQuestionIndex = 1;
    const qNumMatch = qHeader.match(/(?:Question|Câu)\s*(\d+)/i);
    if (qNumMatch) {
        currentQuestionIndex = parseInt(qNumMatch[1], 10);
    } else if (jumpLinks.length > 0) {
        const activeIdx = jumpLis.findIndex(li => li.classList.contains('active') || (container && li.querySelector('a')?.getAttribute('href')?.includes(container.id)));
        if (activeIdx >= 0) {
            currentQuestionIndex = activeIdx + 1;
        }
    } else if (allQuestions.length > 1 && container) {
        const idx = allQuestions.indexOf(container);
        if (idx >= 0) currentQuestionIndex = idx + 1;
    }

    // Navigation buttons
    const candidates = Array.from(document.querySelectorAll('button.next-question, button[name="next"], button.next_question_button, #next-question, button.btn-primary.next-question, [data-automation="sdk-btn-next"]'));
    Array.from(document.querySelectorAll('button, input[type="button"], input[type="submit"], a.btn')).forEach(b => {
        const t = (b.innerText || b.value || '').trim().toLowerCase();
        if ((t.includes('tiếp theo') || t.includes('tiếp') || t === 'next' || t.includes('next >') || t.includes('sau')) && !candidates.includes(b)) {
            candidates.push(b);
        }
    });

    let nextBtn = null;
    let nextBtnExists = false;
    for (const b of candidates) {
        if (b.id === 'submit_quiz_button' || (b.innerText || '').toLowerCase().includes('gửi kiểm tra') || (b.innerText || '').toLowerCase().includes('submit')) {
            continue;
        }
        if (b.offsetParent !== null) {
            nextBtnExists = true;
            if (!b.disabled && !b.hasAttribute('disabled')) {
                nextBtn = {
                    selector: b.id ? `#${b.id}` : (b.className ? `.${b.className.trim().split(/\s+/).join('.')}` : 'button.next-question'),
                    text: (b.innerText || b.value || '').trim()
                };
                break;
            }
        }
    }

    let isLastQuestion = false;
    if (allQuestions.length > 1) {
        isLastQuestion = (currentQuestionIndex >= allQuestions.length);
    } else if (jumpLinks.length > 0) {
        isLastQuestion = (currentQuestionIndex >= jumpLinks.length);
    } else {
        const submitBtn = document.querySelector('#submit_quiz_button, button[id*="submit"], input[id*="submit"]');
        const hasNextCandidates = document.querySelector('button.next-question, button[name="next"], button.next_question_button, #next-question, [data-automation="sdk-btn-next"]');
        if (submitBtn && !hasNextCandidates) {
            isLastQuestion = true;
        }
    }

    const rect = container.getBoundingClientRect();
    const autosaveEl = document.querySelector('#quiz-autosaved-notification, .autosave_status, .auto-save-status');

    return {
        success: true,
        question_id: qId,
        question_type: isDropdownQuestion ? "matching_dropdown" : (isCheckboxQuestion ? "checkbox" : "multiple_choice"),
        header: qHeader,
        text: questionText,
        options: options,
        dropdowns: dropdowns,
        is_dropdown: isDropdownQuestion,
        is_checkbox: isCheckboxQuestion,
        images: images,
        is_single_page: allQuestions.length > 1,
        current_question_index: currentQuestionIndex,
        total_questions: totalQuestions,
        all_question_ids: allQuestions.map(q => q.id),
        has_next: allQuestions.length > 1 ? (currentQuestionIndex < allQuestions.length) : (nextBtnExists || currentQuestionIndex < totalQuestions),
        next_button: nextBtn,
        is_last_question: isLastQuestion,
        autosave_status: autosaveEl ? autosaveEl.innerText.trim() : '',
        bounding_box: {
            x: Math.max(0, rect.x),
            y: Math.max(0, rect.y),
            width: rect.width,
            height: rect.height
        }
    };
})
"""

JS_EXTRACT_QUESTION = f"({JS_EXTRACT_QUESTION_FN})()"

JS_SELECT_RADIO = r"""
((targetOption, targetQId) => {
    // targetOption can be index (1-based, e.g. 1, 2, 3) or letter ("A", "B", "C", "D") or input id
    let container = null;
    if (targetQId) container = document.getElementById(targetQId);
    // Check New Quizzes
    if (!container) {
        const nq = document.querySelector('[data-automation="sdk-take-item-question"]');
        if (nq) container = nq;
    }
    if (!container) {
        const allQuestions = Array.from(document.querySelectorAll('.question.regular_question, .display_question, div[id^="question_"]')).filter(q => {
            if (q.offsetParent === null) return false;
            if (q.classList.contains('question_summary')) return false;
            if (q.id && (q.id.includes('_text') || q.id.includes('_label') || q.id.includes('_answer'))) return false;
            if (!q.querySelector('input[type="radio"], input[type="checkbox"]')) return false;
            if (q.parentElement && q.parentElement.closest('.question, [id^="question_"]')) return false;
            return true;
        });
        if (allQuestions.length === 1) {
            container = allQuestions[0];
        } else if (allQuestions.length > 1) {
            container = allQuestions.find(q => {
                const r = q.getBoundingClientRect();
                return r.top >= -100 && r.top <= window.innerHeight * 0.7;
            }) || allQuestions[0];
        }
    }
    if (!container) container = document;

    const radioInputs = Array.from(container.querySelectorAll('input[type="radio"], input[type="checkbox"]'));
    if (radioInputs.length === 0) {
        return { success: false, error: "No radio buttons or checkboxes found in active question container." };
    }

    // Normalize targets to an array of indices (0-based)
    let targets = [];
    const letters = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H'];

    function parseOneOption(opt) {
        if (typeof opt === 'number') return opt - 1;
        if (typeof opt === 'string') {
            const clean = opt.trim().toUpperCase();
            const lIdx = letters.indexOf(clean);
            if (lIdx >= 0) return lIdx;
            if (/^\d+$/.test(clean)) return parseInt(clean, 10) - 1;
            const found = radioInputs.find(i => i.id === opt || i.value === opt);
            if (found) return radioInputs.indexOf(found);
        }
        return -1;
    }

    if (Array.isArray(targetOption)) {
        targets = targetOption.map(parseOneOption).filter(i => i >= 0 && i < radioInputs.length);
    } else if (typeof targetOption === 'string' && (targetOption.includes(',') || targetOption.includes(' '))) {
        targets = targetOption.split(/[,;\s]+/).map(parseOneOption).filter(i => i >= 0 && i < radioInputs.length);
    } else {
        const single = parseOneOption(targetOption);
        if (single >= 0 && single < radioInputs.length) targets = [single];
    }

    if (targets.length === 0) {
        return {
            success: false,
            error: `Target option '${JSON.stringify(targetOption)}' not found. Available options: ${radioInputs.length}`
        };
    }

    const results = [];
    const isCheckbox = radioInputs.some(i => i.type === 'checkbox');

    // If checkbox, uncheck any currently checked input not in targets
    if (isCheckbox) {
        radioInputs.forEach((inp, idx) => {
            if (!targets.includes(idx) && inp.checked) {
                inp.checked = false;
                inp.dispatchEvent(new Event('input', { bubbles: true, cancelable: true }));
                inp.dispatchEvent(new Event('change', { bubbles: true, cancelable: true }));
            }
        });
    }

    // Select each target input
    targets.forEach(targetIndex => {
        const targetInput = radioInputs[targetIndex];
        let clickTarget = targetInput;
        if (targetInput.id) {
            const lbl = container.querySelector(`label[for="${targetInput.id}"]`);
            if (lbl) clickTarget = lbl;
        }
        if (clickTarget === targetInput) {
            const parentLbl = targetInput.closest('label');
            if (parentLbl) clickTarget = parentLbl;
        }

        const rect = clickTarget.getBoundingClientRect();
        const cx = Math.floor(rect.left + rect.width / 2);
        const cy = Math.floor(rect.top + rect.height / 2);

        ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click'].forEach(evtName => {
            const isDown = evtName.includes('down');
            const evt = new MouseEvent(evtName, {
                view: window,
                bubbles: true,
                cancelable: true,
                clientX: cx,
                clientY: cy,
                button: 0,
                buttons: isDown ? 1 : 0
            });
            clickTarget.dispatchEvent(evt);
        });

        targetInput.checked = true;
        targetInput.dispatchEvent(new Event('input', { bubbles: true, cancelable: true }));
        targetInput.dispatchEvent(new Event('change', { bubbles: true, cancelable: true }));

        if (window.jQuery) {
            try {
                window.jQuery(targetInput).trigger('input').trigger('change');
            } catch (_) {}
        }

        results.push({
            index: targetIndex + 1,
            letter: letters[targetIndex] || `${targetIndex + 1}`,
            id: targetInput.id
        });
    });

    return {
        success: true,
        checked_count: results.length,
        selected_indices: results.map(r => r.index),
        selected_letters: results.map(r => r.letter),
        selected_letter: results.map(r => r.letter).join(', '),
        selected_index: results[0].index
    };
})
"""

JS_SELECT_DROPDOWNS = r"""
((dropdownAnswers, targetQId) => {
    let container = null;
    if (targetQId) container = document.getElementById(targetQId);
    if (!container) {
        container = document.querySelector('.question.regular_question, .display_question, div[id^="question_"]');
    }
    if (!container) container = document;

    const selects = Array.from(container.querySelectorAll('select.question_input, select'));
    if (selects.length === 0) {
        return { success: false, error: "No select/dropdown elements found in question container." };
    }

    let answers = [];
    if (Array.isArray(dropdownAnswers)) {
        answers = dropdownAnswers;
    } else if (typeof dropdownAnswers === 'object' && dropdownAnswers !== null) {
        answers = selects.map((sel, idx) => {
            return dropdownAnswers[sel.id] || dropdownAnswers[sel.name] || dropdownAnswers[String(idx + 1)] || dropdownAnswers[idx] || '';
        });
    } else if (typeof dropdownAnswers === 'string') {
        answers = dropdownAnswers.split(/[,;\n]+/).map(s => s.trim());
    }

    function findOption(selectEl, targetAns) {
        if (!targetAns && targetAns !== 0) return null;
        const cleanTarget = String(targetAns).trim().toLowerCase();
        const opts = Array.from(selectEl.options);

        // 1. Exact text match
        let match = opts.find(o => o.textContent.trim().toLowerCase() === cleanTarget);
        if (match) return match;

        // 2. Exact value match
        match = opts.find(o => o.value.trim().toLowerCase() === cleanTarget);
        if (match) return match;

        // 3. Normalized number / percentage match (e.g. "12%" vs "12 %" or "20,50%" vs "20.50%")
        const normTarget = cleanTarget.replace(/\s+/g, '').replace(',', '.');
        match = opts.find(o => {
            const normOpt = o.textContent.trim().toLowerCase().replace(/\s+/g, '').replace(',', '.');
            return normOpt === normTarget;
        });
        if (match) return match;

        // 4. Substring inclusion match (excluding placeholder)
        match = opts.find(o => {
            if (o.value === '' || /^\[?\s*(choose|chọn|select)\s*\]?$/i.test(o.textContent)) return false;
            const optLower = o.textContent.trim().toLowerCase();
            return optLower.includes(cleanTarget) || cleanTarget.includes(optLower);
        });
        if (match) return match;

        // 5. Index match (1-based non-placeholder index)
        if (/^\d+$/.test(cleanTarget)) {
            const idx = parseInt(cleanTarget, 10);
            const nonPlaceholders = opts.filter(o => o.value !== '' && !/^\[?\s*(choose|chọn|select)\s*\]?$/i.test(o.textContent));
            if (idx >= 1 && idx <= nonPlaceholders.length) {
                return nonPlaceholders[idx - 1];
            }
        }

        return null;
    }

    const results = [];
    selects.forEach((sel, idx) => {
        const targetAns = answers[idx];
        if (targetAns === undefined || targetAns === null || targetAns === '') {
            results.push({ index: idx + 1, id: sel.id, selected: false, reason: "No answer provided" });
            return;
        }

        const matchOpt = findOption(sel, targetAns);
        if (!matchOpt) {
            results.push({
                index: idx + 1,
                id: sel.id,
                selected: false,
                reason: `Could not match answer '${targetAns}' among options: ${Array.from(sel.options).map(o => o.textContent.trim()).join(' | ')}`
            });
            return;
        }

        try {
            const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value').set;
            nativeSetter.call(sel, matchOpt.value);
        } catch (_) {
            sel.value = matchOpt.value;
        }
        sel.selectedIndex = Array.from(sel.options).indexOf(matchOpt);

        sel.dispatchEvent(new Event('input', { bubbles: true }));
        sel.dispatchEvent(new Event('change', { bubbles: true }));

        if (window.jQuery) {
            try {
                window.jQuery(sel).val(matchOpt.value).trigger('change');
            } catch (_) {}
        }

        results.push({
            index: idx + 1,
            id: sel.id,
            selected: true,
            value: matchOpt.value,
            text: matchOpt.textContent.trim()
        });
    });

    const successCount = results.filter(r => r.selected).length;
    return {
        success: successCount > 0,
        total_dropdowns: selects.length,
        selected_count: successCount,
        results: results
    };
})
"""

JS_CLICK_NEXT = r"""
(() => {
    // Locate the Next button
    const candidates = Array.from(document.querySelectorAll('button.next-question, button[name="next"], button.next_question_button, #next-question, button.btn-primary.next-question, [data-automation="sdk-btn-next"]'));
    Array.from(document.querySelectorAll('button, input[type="button"], input[type="submit"], a.btn')).forEach(b => {
        const t = (b.innerText || b.value || '').trim().toLowerCase();
        if ((t.includes('tiếp theo') || t.includes('tiếp') || t === 'next' || t.includes('next >') || t.includes('sau')) && !candidates.includes(b)) {
            candidates.push(b);
        }
    });

    let nextBtn = null;
    for (const b of candidates) {
        // Exclude submit button
        if (b.id === 'submit_quiz_button' || (b.innerText || '').toLowerCase().includes('gửi kiểm tra') || (b.innerText || '').toLowerCase().includes('submit')) {
            continue;
        }
        if (b.offsetParent !== null) {
            nextBtn = b;
            break;
        }
    }

    if (nextBtn) {
        // Scroll into view & click
        nextBtn.scrollIntoView({ behavior: 'smooth', block: 'center' });
        const rect = nextBtn.getBoundingClientRect();
        const cx = Math.floor(rect.left + rect.width / 2);
        const cy = Math.floor(rect.top + rect.height / 2);

        ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click'].forEach(evtName => {
            nextBtn.dispatchEvent(new MouseEvent(evtName, {
                view: window,
                bubbles: true,
                cancelable: true,
                clientX: cx,
                clientY: cy,
                button: 0,
                buttons: evtName.includes('down') ? 1 : 0
            }));
        });
        nextBtn.click();

        return {
            success: true,
            method: "next_button",
            button_text: (nextBtn.innerText || nextBtn.value || '').trim()
        };
    }

    // Fallback: Check jump list in sidebar to click the next question link
    const jumpLis = Array.from(document.querySelectorAll('#right-side .question_list li, #quiz_question_list li'));
    const currentActiveIdx = jumpLis.findIndex(li => li.classList.contains('active'));

    if (currentActiveIdx >= 0 && currentActiveIdx + 1 < jumpLis.length) {
        const nextLink = jumpLis[currentActiveIdx + 1].querySelector('a');
        if (nextLink) {
            nextLink.click();
            return { success: true, method: "sidebar_jump", target: nextLink.innerText.trim() };
        }
    }

    // Further fallback: find first unanswered item in jump list
    const unansweredLi = jumpLis.find(li => !li.classList.contains('answered'));
    if (unansweredLi) {
        const link = unansweredLi.querySelector('a');
        if (link) {
            link.click();
            return { success: true, method: "sidebar_jump_unanswered", target: link.innerText.trim() };
        }
    }

    return { success: false, error: "Next button not found or already on final question." };
})()
"""

# ---------------------------------------------------------------------------
# AI Question Solver Implementation
# ---------------------------------------------------------------------------

SOLVER_SYSTEM_PROMPT = """You are an expert academic tutor and exam-solving engine specializing in Vietnamese and CIE A-Level Physics, Mathematics, Chemistry, Natural Sciences, Economics, etc.

Your task is to analyze the question and determine the EXACT correct answer(s).

Requirements:
1. Examine the question statement carefully. Pay special attention to formulas, units, tables, economic quantities, definitions, and problem constraints.
2. If this is a multiple-choice question: evaluate options A, B, C, D (1, 2, 3, 4).
3. If this question has checkboxes or multiple correct answers: provide ALL matching options in selected_options and selected_letters.
4. If this is a MATCHING or MULTIPLE DROPDOWN question:
   You MUST provide "dropdown_answers": an array of the exact matching text strings (or option values) for EACH dropdown item in order (Item 1, Item 2, ..., Item N).
5. Output your final response in STRICT JSON format matching this schema:
{
  "reasoning": "Clear, concise 2-3 sentence explanation identifying the calculations, economic concepts, and matching rationale.",
  "selected_options": [1], // Array of 1-indexed integers (for radio/checkbox)
  "selected_letters": ["A"], // Array of letters (for radio/checkbox)
  "selected_option": 1, // Primary 1-indexed integer or first option
  "selected_letter": "A", // Primary letter or first option
  "dropdown_answers": ["Match 1", "Match 2"], // Array of exact answer text strings for each dropdown item in order
  "confidence": 0.95 // Float between 0.0 and 1.0
}
Do not wrap in markdown quotes if possible, output valid JSON only.
"""

def format_question_prompt(q_data: Dict[str, Any]) -> str:
    parts = []
    if q_data.get("header"):
        parts.append(f"[{q_data['header']}]")
    parts.append(q_data.get("text", ""))

    dropdowns = q_data.get("dropdowns", [])
    if q_data.get("is_dropdown") and dropdowns:
        parts.append("\n[Question Type: Matching / Dropdown Selections]")
        parts.append(f"There are {len(dropdowns)} dropdown items. For EACH item, select the exact matching answer from its available choices:\n")
        for dd in dropdowns:
            choices_str = ", ".join(f"'{c}'" for c in dd.get("available_choices", []))
            parts.append(f"Item {dd.get('index')}: \"{dd.get('label')}\"")
            parts.append(f"  Available choices: [{choices_str}]\n")
    else:
        opts = q_data.get("options", [])
        if opts:
            parts.append("\nAnswer Options:")
            for opt in opts:
                t = f" [{opt.get('type')}]" if opt.get('type') else ""
                parts.append(f"{opt.get('letter')}) {opt.get('text')}{t}")

    if q_data.get("images"):
        parts.append("\nEmbedded Figures:")
        for img in q_data["images"]:
            parts.append(f"- Image: {img.get('src')} (Alt: {img.get('alt')})")

    return "\n".join(parts)


def parse_solver_json(raw_text: str) -> Dict[str, Any]:
    clean = raw_text.strip()
    if clean.startswith("```json"):
        clean = clean[7:]
    elif clean.startswith("```"):
        clean = clean[3:]
    if clean.endswith("```"):
        clean = clean[:-3]
    clean = clean.strip()

    letter_to_idx = {"A": 1, "B": 2, "C": 3, "D": 4, "E": 5, "F": 6}

    try:
        data = json.loads(clean)
        # Normalize fields
        opts = data.get("selected_options") or ([data.get("selected_option")] if data.get("selected_option") else [])
        letters = data.get("selected_letters") or ([data.get("selected_letter")] if data.get("selected_letter") else [])
        if letters and not opts:
            opts = [letter_to_idx.get(l.upper(), 1) for l in letters if l]
        if opts and not letters:
            idx_to_letter = {v: k for k, v in letter_to_idx.items()}
            letters = [idx_to_letter.get(i, str(i)) for i in opts]

        data["selected_options"] = opts
        data["selected_letters"] = letters
        data["selected_option"] = opts[0] if opts else 1
        data["selected_letter"] = letters[0] if letters else "A"

        # Normalize dropdown_answers
        dd_answers = data.get("dropdown_answers") or data.get("dropdowns") or data.get("matches") or []
        if isinstance(dd_answers, dict):
            sorted_keys = sorted(dd_answers.keys(), key=lambda k: int(k) if str(k).isdigit() else k)
            dd_answers = [dd_answers[k] for k in sorted_keys]
        elif not isinstance(dd_answers, list):
            dd_answers = [str(dd_answers)]
        data["dropdown_answers"] = dd_answers

        return data
    except Exception:
        # Fallback regex extraction
        opts = [int(x) for x in re.findall(r'"selected_option[s]?":\s*\[?(\d+)', raw_text)]
        letters = re.findall(r'\b([A-D])\b', raw_text)
        reason_match = re.search(r'"reasoning":\s*"([^"]+)"', raw_text)

        if not opts and letters:
            opts = [letter_to_idx.get(l.upper(), 1) for l in letters]

        if not opts:
            opts = [1]
            letters = ["A"]

        dd_matches = re.findall(r'"dropdown_answers":\s*\[(.*?)\]', raw_text, re.DOTALL)
        dd_answers = []
        if dd_matches:
            dd_answers = [s.strip().strip('"').strip("'") for s in dd_matches[0].split(',') if s.strip()]

        return {
            "reasoning": reason_match.group(1) if reason_match else raw_text[:200],
            "selected_options": opts,
            "selected_letters": letters,
            "selected_option": opts[0],
            "selected_letter": letters[0],
            "dropdown_answers": dd_answers,
            "confidence": 0.8
        }


async def solve_with_gateway(prompt_text: str, gateway_url: str = DEFAULT_GATEWAY_URL, model: str = "gemini-3.8-flash-tiered") -> Dict[str, Any]:
    """Calls local Anthropic-compatible API gateway (e.g. localhost:8080)."""
    endpoint = f"{gateway_url.rstrip('/')}/v1/messages"
    payload = {
        "model": model,
        "max_tokens": 2048,
        "system": SOLVER_SYSTEM_PROMPT,
        "messages": [
            {"role": "user", "content": prompt_text}
        ]
    }
    headers = {
        "Content-Type": "application/json",
        "x-api-key": "test",
        "anthropic-version": "2023-06-01"
    }

    req = urllib.request.Request(endpoint, data=json.dumps(payload).encode("utf-8"), headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        content = data.get("content", [])
        text = ""
        for block in content:
            if block.get("type") == "text":
                text += block.get("text", "")
        return parse_solver_json(text)


async def solve_with_gemini(prompt_text: str, api_key: str, model: str = "gemini-2.5-flash") -> Dict[str, Any]:
    """Direct Google Gemini API fallback."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload = {
        "systemInstruction": {
            "parts": [{"text": SOLVER_SYSTEM_PROMPT}]
        },
        "contents": [
            {"role": "user", "parts": [{"text": prompt_text}]}
        ],
        "generationConfig": {
            "maxOutputTokens": 800,
            "responseMimeType": "application/json"
        }
    }
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, context=SSL_CTX, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        candidate = data.get("candidates", [{}])[0]
        text = candidate.get("content", {}).get("parts", [{}])[0].get("text", "")
        return parse_solver_json(text)


async def solve_with_openrouter(prompt_text: str, api_key: str, model: str = "google/gemini-2.5-flash") -> Dict[str, Any]:
    """OpenRouter API fallback."""
    url = "https://openrouter.ai/api/v1/chat/completions"
    payload = {
        "model": model,
        "max_tokens": 2048,
        "messages": [
            {"role": "system", "content": SOLVER_SYSTEM_PROMPT},
            {"role": "user", "content": prompt_text}
        ]
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers)
    with urllib.request.urlopen(req, context=SSL_CTX, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        text = data["choices"][0]["message"]["content"]
        return parse_solver_json(text)


async def solve_question(q_data: Dict[str, Any], backend: str = "auto") -> Dict[str, Any]:
    """Unified solver dispatcher with automatic fallbacks and retries."""
    prompt = format_question_prompt(q_data)
    last_err = None

    # 1. Try Local Gateway (default for Claude Code environment)
    if backend in ("auto", "gateway", "local"):
        for attempt in range(2):
            try:
                return await solve_with_gateway(prompt)
            except Exception as e:
                last_err = e
                if attempt == 0:
                    await asyncio.sleep(0.5)
                    continue
                if backend != "auto":
                    raise RuntimeError(f"Local gateway failed: {e}")

    # 2. Try OpenRouter if API key is set
    openrouter_key = os.environ.get("OPENROUTER_API_KEY") or OPENROUTER_KEY
    if backend in ("auto", "openrouter") and openrouter_key:
        try:
            return await solve_with_openrouter(prompt, openrouter_key)
        except Exception as e:
            last_err = e
            if backend != "auto":
                raise RuntimeError(f"OpenRouter API failed: {e}")

    # 3. Try Gemini with stored extension key or env
    if backend in ("auto", "gemini"):
        gemini_key = os.environ.get("GEMINI_API_KEY") or await get_extension_gemini_key()
        if gemini_key:
            try:
                return await solve_with_gemini(prompt, gemini_key)
            except Exception as e:
                last_err = e
                if backend != "auto":
                    raise RuntimeError(f"Gemini API failed: {e}")

    err_detail = f" (Last error: {last_err})" if last_err else ""
    raise RuntimeError(f"No working solver backend available. Ensure local gateway is running or set GEMINI_API_KEY / OPENROUTER_API_KEY.{err_detail}")


# ---------------------------------------------------------------------------
# Tab Discovery & Automated Quiz Starting
# ---------------------------------------------------------------------------

async def inspect_tab_quiz(tab: Dict[str, Any]) -> Dict[str, Any]:
    """Inspects a single tab to determine whether it is an active quiz, ready to start, or retake."""
    ws_url = tab.get("webSocketDebuggerUrl")
    if not ws_url:
        return {
            "tab": tab,
            "tab_id": tab.get("id"),
            "title": tab.get("title", ""),
            "url": tab.get("url", ""),
            "is_quiz_page": False,
            "is_taking": False,
            "can_start": False,
            "start_button": None,
            "ignored_again_button": None,
            "error": "No webSocketDebuggerUrl"
        }

    try:
        async with CanvasCDPClient(ws_url) as client:
            res = await client.eval_js(JS_INSPECT_TAB_QUIZ)
            if not isinstance(res, dict):
                res = {}
            return {
                "tab": tab,
                "tab_id": tab.get("id"),
                "title": res.get("title") or tab.get("title", ""),
                "url": res.get("url") or tab.get("url", ""),
                "is_quiz_page": res.get("is_quiz_page", False),
                "is_taking": res.get("is_taking", False),
                "can_start": res.get("can_start", False),
                "start_button": res.get("start_button"),
                "ignored_again_button": res.get("ignored_again_button"),
                "time_limit": res.get("time_limit"),
                "allowed_attempts": res.get("allowed_attempts")
            }
    except Exception as e:
        url = tab.get("url", "")
        is_take = "/quizzes/" in url and "/take" in url
        is_quiz = "/quizzes/" in url
        return {
            "tab": tab,
            "tab_id": tab.get("id"),
            "title": tab.get("title", ""),
            "url": url,
            "is_quiz_page": is_quiz,
            "is_taking": is_take,
            "can_start": is_quiz and not is_take,
            "start_button": None,
            "ignored_again_button": None,
            "error": str(e)
        }


async def find_all_quiz_tabs(cdp_host: str = CDP_HOST, cdp_port: int = CDP_PORT) -> Dict[str, Any]:
    """
    Searches for quizzes across all open tabs.
    Ignores tabs that only have 'Take the Quiz Again' buttons.
    Returns categorized tabs dictionary.
    """
    pages = await get_all_page_targets(cdp_host, cdp_port)
    if not pages:
        return {
            "all_pages": [],
            "taking": [],
            "ready_to_start": [],
            "ignored_retake": [],
            "actionable": [],
            "other": []
        }

    inspections = await asyncio.gather(*(inspect_tab_quiz(p) for p in pages), return_exceptions=True)

    taking = []
    ready_to_start = []
    ignored_retake = []
    other = []

    for item in inspections:
        if isinstance(item, Exception) or not isinstance(item, dict):
            continue
        if item.get("is_taking"):
            taking.append(item)
        elif item.get("can_start"):
            ready_to_start.append(item)
        elif item.get("ignored_again_button") and not item.get("start_button"):
            ignored_retake.append(item)
        else:
            other.append(item)

    actionable = taking + ready_to_start
    return {
        "all_pages": [i for i in inspections if isinstance(i, dict)],
        "taking": taking,
        "ready_to_start": ready_to_start,
        "ignored_retake": ignored_retake,
        "actionable": actionable,
        "other": other
    }


async def start_quiz_on_tab(tab_info: Dict[str, Any], max_wait_sec: float = 15.0) -> Dict[str, Any]:
    """
    Automatically clicks 'Take the Quiz' on an unstarted quiz tab (ignoring 'Take again').
    Waits for the page to navigate to /take and load questions.
    """
    ws_url = tab_info.get("tab", {}).get("webSocketDebuggerUrl") or tab_info.get("webSocketDebuggerUrl")
    if not ws_url:
        return {"success": False, "error": "Tab has no WebSocket debugger URL"}

    tab_title = tab_info.get("title", "Quiz")
    start_btn = tab_info.get("start_button") or {}
    btn_text = start_btn.get("text", "Take the Quiz") if start_btn else "Take the Quiz"

    print(f"  -> Activating '{btn_text}' on '{tab_title[:50]}'...")

    # Step 1: Connect and trigger start click
    async with CanvasCDPClient(ws_url) as client:
        try:
            await client.send_command("Page.bringToFront")
        except Exception:
            pass

        click_res = await client.eval_js(JS_START_QUIZ)
        if not click_res.get("success"):
            return {
                "success": False,
                "error": click_res.get("error", "Failed to click start quiz button")
            }

    # Step 2: Poll for navigation to /take or question elements appearing
    start_time = time.time()
    await asyncio.sleep(0.8)

    while (time.time() - start_time) < max_wait_sec:
        await asyncio.sleep(0.5)
        try:
            async with CanvasCDPClient(ws_url) as client:
                ready_check = await client.eval_js(JS_CHECK_QUIZ_READY)
                if ready_check and (ready_check.get("ready") or ready_check.get("hasQuestions")):
                    tab_info["is_taking"] = True
                    tab_info["can_start"] = False
                    tab_info["url"] = ready_check.get("url", tab_info.get("url"))
                    return {
                        "success": True,
                        "title": tab_title,
                        "url": ready_check.get("url"),
                        "question_count": ready_check.get("questionCount", 0)
                    }
        except Exception:
            # During page reload / context destruction, connection or evaluate can transiently fail
            continue

    return {
        "success": False,
        "error": f"Timed out waiting for quiz to start after {max_wait_sec}s"
    }


async def search_and_start_all_quizzes(cdp_host: str = CDP_HOST, cdp_port: int = CDP_PORT, max_wait_sec: float = 15.0) -> List[Dict[str, Any]]:
    """
    Searches all open tabs, ignores 'take the quiz again' buttons,
    and automatically starts all quizzes that are ready.
    """
    scan_res = await find_all_quiz_tabs(cdp_host, cdp_port)
    ready = scan_res["ready_to_start"]
    results = []

    for t in ready:
        res = await start_quiz_on_tab(t, max_wait_sec=max_wait_sec)
        results.append({
            "tab_id": t.get("tab_id"),
            "title": t.get("title"),
            "result": res
        })

    return results


async def solve_quiz_on_client(client: CanvasCDPClient, tab_info: Dict[str, Any], args: argparse.Namespace) -> Dict[str, Any]:
    """Runs the auto-solve answering loop on an active quiz tab."""
    max_q = args.max_questions or 100
    delay = args.delay
    backend = args.backend

    initial_data = await client.eval_js(JS_EXTRACT_QUESTION)
    if not initial_data.get("success"):
        return {"success": False, "error": initial_data.get("error")}

    is_single_page = initial_data.get("is_single_page", False)
    all_q_ids = initial_data.get("all_question_ids", [])

    # MODE 1: Single-Page Quiz (All questions rendered on one page)
    if is_single_page and len(all_q_ids) > 1:
        total = len(all_q_ids)
        print(f"  Detected Single-Page Quiz layout: {total} questions on page.")
        for idx, q_id in enumerate(all_q_ids, 1):
            if idx > max_q:
                break
            print(f"\n  [{idx}/{total}] Question {q_id}...")
            q_data = await client.eval_js(f"({JS_EXTRACT_QUESTION_FN})({json.dumps(q_id)})")
            if not q_data.get("success"):
                print(f"    Warning: failed to extract {q_id}: {q_data.get('error')}")
                continue

            solution = await solve_question(q_data, backend=backend)
            if q_data.get("is_dropdown"):
                dd_answers = solution.get("dropdown_answers") or solution.get("selected_options") or []
                print(f"    -> AI Dropdown Choices: {dd_answers} | Confidence: {solution.get('confidence')}")
                select_expr = f"({JS_SELECT_DROPDOWNS})({json.dumps(dd_answers)}, {json.dumps(q_id)})"
                select_res = await client.eval_js(select_expr)
                if not select_res.get("success"):
                    print(f"    Error selecting dropdowns: {select_res.get('error')}")
                else:
                    print(f"    -> {select_res.get('selected_count')}/{select_res.get('total_dropdowns')} dropdown(s) selected for {q_id}.")
            else:
                target_opt = solution.get("selected_options") or solution.get("selected_option", 1)
                letter = solution.get("selected_letters") or solution.get("selected_letter", "A")
                print(f"    -> AI Choice: Option {letter} ({target_opt}) | Confidence: {solution.get('confidence')}")

                select_expr = f"({JS_SELECT_RADIO})({json.dumps(target_opt)}, {json.dumps(q_id)})"
                select_res = await client.eval_js(select_expr)
                if not select_res.get("success"):
                    print(f"    Error selecting options: {select_res.get('error')}")
                else:
                    print(f"    -> Option(s) checked for {q_id}.")

            if delay > 0:
                await asyncio.sleep(delay)
            else:
                await asyncio.sleep(0.1)

        print(f"\n  === Completed all {len(all_q_ids)} questions on page ===")
        print("  Quiz finished answering! Not auto-submitting for student review.")
        return {"success": True, "questions_answered": len(all_q_ids), "mode": "single_page"}

    # MODE 2: Sequential Quiz (One Question at a Time with Next / Jump List)
    count = 0
    last_q_id = None
    consecutive_same_q = 0

    while count < max_q:
        count += 1
        data = await client.eval_js(JS_EXTRACT_QUESTION)
        if not data.get("success"):
            print(f"  Extraction halted: {data.get('error')}")
            break

        current_q_id = data.get("question_id")
        current_idx = data.get("current_question_index", count)
        total_q = data.get("total_questions", "?")
        header = data.get("header") or f"Question {current_idx}"

        # Safeguard: detect if stalled on the same question
        if current_q_id == last_q_id:
            consecutive_same_q += 1
            if consecutive_same_q >= 3:
                print(f"  Warning: Question {current_q_id} did not advance. Attempting jump list...")
                await client.eval_js(JS_CLICK_NEXT)
                await asyncio.sleep(1.0)
                consecutive_same_q = 0
                continue
        else:
            consecutive_same_q = 0

        last_q_id = current_q_id
        print(f"\n  [{count}] Processing {header} ({current_idx}/{total_q})...")

        solution = await solve_question(data, backend=backend)
        if data.get("is_dropdown"):
            dd_answers = solution.get("dropdown_answers") or solution.get("selected_options") or []
            print(f"    -> AI Dropdown Choices: {dd_answers} | Confidence: {solution.get('confidence')}")
            select_expr = f"({JS_SELECT_DROPDOWNS})({json.dumps(dd_answers)}, {json.dumps(current_q_id)})"
            select_res = await client.eval_js(select_expr)
            if not select_res.get("success"):
                print(f"    Error selecting dropdowns: {select_res.get('error')}")
                break
            print(f"    -> {select_res.get('selected_count')}/{select_res.get('total_dropdowns')} dropdown(s) selected & autosave triggered.")
        else:
            target_opt = solution.get("selected_options") or solution.get("selected_option", 1)
            letter = solution.get("selected_letters") or solution.get("selected_letter", "A")
            print(f"    -> AI Choice: Option {letter} ({target_opt}) | Confidence: {solution.get('confidence')}")

            select_expr = f"({JS_SELECT_RADIO})({json.dumps(target_opt)}, {json.dumps(current_q_id)})"
            select_res = await client.eval_js(select_expr)
            if not select_res.get("success"):
                print(f"    Error selecting options: {select_res.get('error')}")
                break

            print("    -> Option(s) checked & autosave triggered.")

        has_next = data.get("has_next", False) or (data.get("next_button") is not None)
        if not has_next:
            await asyncio.sleep(0.6)
            recheck = await client.eval_js(JS_EXTRACT_QUESTION)
            if recheck.get("has_next") or recheck.get("next_button"):
                data = recheck
                continue
            print(f"\n  === Reached final question (Question {current_idx}) ===")
            print("  Quiz finished answering! Not auto-submitting for student review.")
            return {"success": True, "questions_answered": count, "mode": "sequential"}

        if delay > 0:
            print(f"    -> Waiting {delay}s...")
            await asyncio.sleep(delay)

        next_res = await client.eval_js(JS_CLICK_NEXT)
        if not next_res.get("success"):
            print(f"    Note on navigation: {next_res.get('error')}")

        transitioned = False
        for poll_attempt in range(30):
            await asyncio.sleep(0.2)
            try:
                check_data = await client.eval_js(JS_EXTRACT_QUESTION)
                if check_data.get("success"):
                    if check_data.get("question_id") != current_q_id and len(check_data.get("options", [])) > 0:
                        transitioned = True
                        break
                    if check_data.get("is_last_question") and not check_data.get("has_next"):
                        transitioned = True
                        break
            except Exception:
                await asyncio.sleep(0.3)
                continue

            if poll_attempt == 15:
                print("    -> Retrying navigation trigger...")
                await client.eval_js(JS_CLICK_NEXT)

        if not transitioned:
            print("    -> Proceeding to next check...")

    print(f"\n  Completed auto-solve loop ({count} steps).")
    return {"success": True, "questions_answered": count, "mode": "sequential"}


# ---------------------------------------------------------------------------
# CLI Command Implementations
# ---------------------------------------------------------------------------

async def cmd_status(args):
    """Reports status of quiz tabs across Chrome, or detailed status of current active quiz."""
    res = await find_all_quiz_tabs()
    pages = res["all_pages"]
    taking = res["taking"]
    ready = res["ready_to_start"]
    ignored = res["ignored_retake"]

    print(f"=== Chrome CDP Quiz Status ({len(pages)} open tabs) ===")
    print(f"  In Progress: {len(taking)} | Ready to Start: {len(ready)} | Ignored Retakes: {len(ignored)}")

    for idx, p in enumerate(pages, 1):
        title = p.get("title", "")[:50]
        if p in taking:
            st = "[TAKING]"
        elif p in ready:
            btn_t = p.get("start_button", {}).get("text", "Take the Quiz")
            st = f"[READY: '{btn_t}']"
        elif p in ignored:
            btn_t = p.get("ignored_again_button", {}).get("text", "Take the Quiz Again")
            st = f"[IGNORED: '{btn_t}']"
        else:
            st = "[OTHER]"
        print(f"  [{idx}] {st:<22} {title}")

    try:
        page = await get_canvas_page_info()
        print(f"\nPrimary Target: {page.get('title')}")
        print(f"URL: {page.get('url')}")
        async with CanvasCDPClient(page["webSocketDebuggerUrl"]) as client:
            q_res = await client.eval_js(JS_EXTRACT_QUESTION)
            if q_res.get("error"):
                print(f"Question State: {q_res['error']}")
            else:
                print(f"Active Question: {q_res.get('header', 'Question')} (Total: {q_res.get('total_questions')})")
                print(f"Radio Options Count: {len(q_res.get('options', []))}")
                print(f"Has Next Button: {q_res.get('has_next')} | Is Final: {q_res.get('is_last_question')}")
                if q_res.get("autosave_status"):
                    print(f"Autosave: {q_res.get('autosave_status')}")
    except Exception as e:
        print(f"\nNote on primary target inspection: {e}")


async def cmd_search(args):
    """Searches and lists quizzes across all open tabs, ignoring 'Take the Quiz Again' buttons."""
    print("=== Scanning All Open Browser Tabs for Quizzes ===")
    res = await find_all_quiz_tabs()
    pages = res["all_pages"]
    taking = res["taking"]
    ready = res["ready_to_start"]
    ignored = res["ignored_retake"]

    print(f"\nTotal open tabs: {len(pages)}")
    print(f"  - Active / In Progress Quizzes: {len(taking)}")
    print(f"  - Ready to Start Quizzes:       {len(ready)}")
    print(f"  - Ignored ('Take Again') Tabs:   {len(ignored)}")
    print(f"  - Non-Quiz Tabs:                 {len(res['other'])}")

    print("\nDetailed Tab Listing:")
    for idx, p in enumerate(pages, 1):
        title = p.get("title", "")[:60]
        url = p.get("url", "")
        if p in taking:
            status = "TAKING"
            details = "Active quiz taking interface"
        elif p in ready:
            status = "READY"
            btn_t = p.get("start_button", {}).get("text", "Take the Quiz")
            details = f"Start Button: '{btn_t}'"
        elif p in ignored:
            status = "IGNORED"
            btn_t = p.get("ignored_again_button", {}).get("text", "Take the Quiz Again")
            details = f"Retake Button: '{btn_t}' (Skipped as requested)"
        else:
            status = "OTHER"
            details = "Not a Canvas quiz"

        print(f"  [{idx}] [{status:<7}] {title}")
        print(f"        URL: {url}")
        print(f"        Info: {details}")


async def cmd_start(args):
    """Searches all tabs, ignores 'Take the Quiz Again' buttons, and starts all ready quizzes."""
    print("=== Searching and Starting All Ready Quizzes Across All Tabs ===")
    res = await find_all_quiz_tabs()
    ready = res["ready_to_start"]
    ignored = res["ignored_retake"]

    if ignored:
        print(f"Notice: Ignored {len(ignored)} tab(s) with 'Take the Quiz Again' / retake buttons.")

    if getattr(args, "tab", None) is not None:
        tab_arg = str(args.tab).strip()
        if tab_arg.isdigit():
            idx = int(tab_arg) - 1
            if 0 <= idx < len(res["all_pages"]):
                target = res["all_pages"][idx]
                ready = [target] if target in ready else []
        else:
            ready = [t for t in ready if t.get("tab_id") == tab_arg or tab_arg in t.get("url", "")]

    if not ready:
        print("No quizzes ready to start found on any open tab.")
        if ignored:
            print("(Tabs with 'Take the Quiz Again' buttons were ignored as requested.)")
        return

    print(f"\nFound {len(ready)} quiz(zes) ready to start:")
    for idx, t in enumerate(ready, 1):
        print(f"  {idx}. {t.get('title')[:60]}")

    for idx, t in enumerate(ready, 1):
        print(f"\n[{idx}/{len(ready)}] Starting: {t.get('title')[:55]}...")
        start_res = await start_quiz_on_tab(t)
        if start_res.get("success"):
            print(f"  -> Successfully started! URL: {start_res.get('url')}")
        else:
            print(f"  -> Failed to start: {start_res.get('error')}")

    print("\nStart operation complete.")


async def cmd_extract(args):
    page = await get_canvas_page_info()
    async with CanvasCDPClient(page["webSocketDebuggerUrl"]) as client:
        data = await client.eval_js(JS_EXTRACT_QUESTION)
        print(json.dumps(data, indent=2, ensure_ascii=False))


async def cmd_solve(args):
    page = await get_canvas_page_info()
    async with CanvasCDPClient(page["webSocketDebuggerUrl"]) as client:
        data = await client.eval_js(JS_EXTRACT_QUESTION)
        if not data.get("success"):
            print(f"Failed to extract question: {data.get('error')}", file=sys.stderr)
            sys.exit(1)

        print(f"=== {data.get('header', 'Question')} ===")
        print(data.get("text"))

        if data.get("is_dropdown"):
            print("\nDropdown Items & Choices:")
            for dd in data.get("dropdowns", []):
                print(f"  {dd.get('index')}. {dd.get('label')}")
                print(f"     Choices: {dd.get('available_choices')}")
        else:
            print("\nOptions:")
            for opt in data.get("options", []):
                print(f"  {opt['letter']}) {opt['text']}")

        print("\n[Solving with AI...]")
        solution = await solve_question(data, backend=args.backend)
        print("\n=== Solution ===")
        if data.get("is_dropdown"):
            print(f"Selected Dropdown Answers: {solution.get('dropdown_answers')}")
        else:
            print(f"Selected Option: {solution.get('selected_letter')} (Index {solution.get('selected_option')})")
        print(f"Reasoning: {solution.get('reasoning')}")
        print(f"Confidence: {solution.get('confidence')}")


async def cmd_answer(args):
    page = await get_canvas_page_info()
    async with CanvasCDPClient(page["webSocketDebuggerUrl"]) as client:
        choice = args.option
        data = await client.eval_js(JS_EXTRACT_QUESTION)
        if data.get("is_dropdown"):
            try:
                answers = json.loads(choice)
            except Exception:
                answers = [s.strip() for s in choice.split(",") if s.strip()]
            expr = f"({JS_SELECT_DROPDOWNS})({json.dumps(answers)}, {json.dumps(data.get('question_id'))})"
            res = await client.eval_js(expr)
            if not res.get("success"):
                print(f"Error selecting dropdowns: {res.get('error')}", file=sys.stderr)
                sys.exit(1)
            print(f"Successfully selected {res.get('selected_count')}/{res.get('total_dropdowns')} dropdown(s)")
            for r in res.get("results", []):
                if r.get("selected"):
                    print(f"  Dropdown {r.get('index')}: '{r.get('text')}'")
        else:
            expr = f"({JS_SELECT_RADIO})({json.dumps(choice)})"
            res = await client.eval_js(expr)
            if not res.get("success"):
                print(f"Error selecting answer: {res.get('error')}", file=sys.stderr)
                sys.exit(1)

            print(f"Successfully selected Option {res.get('selected_letter')} ({res.get('selected_index')})")
            if res.get("label_text"):
                print(f"Label: {res.get('label_text')}")

        if args.next:
            print(f"Waiting {args.delay}s for Canvas autosave sync...")
            await asyncio.sleep(args.delay)
            next_res = await client.eval_js(JS_CLICK_NEXT)
            if next_res.get("success"):
                print(f"Clicked Next ({next_res.get('button_text', next_res.get('method'))})")
            else:
                print(f"Next button info: {next_res.get('error')}")


async def cmd_next(args):
    page = await get_canvas_page_info()
    async with CanvasCDPClient(page["webSocketDebuggerUrl"]) as client:
        res = await client.eval_js(JS_CLICK_NEXT)
        if res.get("success"):
            print(f"Advanced to next question ({res.get('button_text', res.get('method'))})")
        else:
            print(f"Could not click Next: {res.get('error')}", file=sys.stderr)
            sys.exit(1)


async def cmd_step(args):
    """Unified step: Extract -> Solve -> Select radio/dropdown -> Delay -> Click Next."""
    page = await get_canvas_page_info()
    async with CanvasCDPClient(page["webSocketDebuggerUrl"]) as client:
        data = await client.eval_js(JS_EXTRACT_QUESTION)
        if not data.get("success"):
            print(f"Failed to extract question: {data.get('error')}", file=sys.stderr)
            sys.exit(1)

        print(f"\n[Active Question: {data.get('header', 'Question')}]")
        print(data.get("text")[:150] + "..." if len(data.get("text", "")) > 150 else data.get("text"))

        solution = await solve_question(data, backend=args.backend)
        if data.get("is_dropdown"):
            dd_answers = solution.get("dropdown_answers") or solution.get("selected_options") or []
            print(f"AI Selected Dropdown Answers: {dd_answers}")
            print(f"Reasoning: {solution.get('reasoning')}")
            select_expr = f"({JS_SELECT_DROPDOWNS})({json.dumps(dd_answers)}, {json.dumps(data.get('question_id'))})"
            select_res = await client.eval_js(select_expr)
            if not select_res.get("success"):
                print(f"Error selecting dropdowns: {select_res.get('error')}", file=sys.stderr)
                sys.exit(1)
            print(f"Selected {select_res.get('selected_count')}/{select_res.get('total_dropdowns')} dropdown(s).")
            for r in select_res.get("results", []):
                if r.get("selected"):
                    print(f"  Dropdown {r.get('index')}: '{r.get('text')}'")
        else:
            target_opt = solution.get("selected_options") or solution.get("selected_option", 1)
            letter = solution.get("selected_letters") or solution.get("selected_letter", "A")

            print(f"AI Selected: Option {letter} ({target_opt})")
            print(f"Reasoning: {solution.get('reasoning')}")

            # Input radio / checkboxes
            select_expr = f"({JS_SELECT_RADIO})({json.dumps(target_opt)}, {json.dumps(data.get('question_id'))})"
            select_res = await client.eval_js(select_expr)
            if not select_res.get("success"):
                print(f"Error selecting answer: {select_res.get('error')}", file=sys.stderr)
                sys.exit(1)
            print("Selection confirmed and verified.")

        if not args.no_next and not data.get("is_last_question"):
            if args.delay > 0:
                print(f"Waiting {args.delay}s before clicking Next...")
                await asyncio.sleep(args.delay)
            next_res = await client.eval_js(JS_CLICK_NEXT)
            if next_res.get("success"):
                print("Clicked Next question successfully.")
            else:
                print(f"Note: {next_res.get('error')}")
        elif data.get("is_last_question"):
            print("Reached final question. Quiz ready for review / manual submission.")


async def cmd_auto(args):
    """
    Automated multi-tab Canvas quiz workflow:
    1. Searches for quizzes across all open tabs.
    2. Ignores 'Take the Quiz Again' / retake buttons.
    3. Automatically starts unstarted quizzes on each tab.
    4. Automatically solves questions until each quiz is completed.
    """
    cdp_host = CDP_HOST
    cdp_port = CDP_PORT

    print("\n" + "=" * 75)
    print("  CANVAS AUTO MODE: SCANNING ALL OPEN TABS FOR QUIZZES")
    print("=" * 75)

    scan_res = await find_all_quiz_tabs(cdp_host, cdp_port)
    all_pages = scan_res["all_pages"]
    taking = scan_res["taking"]
    ready_to_start = scan_res["ready_to_start"]
    ignored_retake = scan_res["ignored_retake"]
    actionable = scan_res["actionable"]

    print(f"Discovered {len(all_pages)} open browser tab(s):")
    for idx, item in enumerate(all_pages, 1):
        title = item.get("title", "Untitled")[:55]
        if item in taking:
            status_tag = "[IN PROGRESS / TAKING]"
        elif item in ready_to_start:
            btn_t = item.get("start_button", {}).get("text", "Take the Quiz")
            status_tag = f"[READY TO START] (Button: '{btn_t}')"
        elif item in ignored_retake:
            btn_t = item.get("ignored_again_button", {}).get("text", "Take the Quiz Again")
            status_tag = f"[IGNORED] (Retake Button: '{btn_t}' - Skipped)"
        else:
            status_tag = "[NON-QUIZ]"
        print(f"  [{idx}] {status_tag} {title}")

    if ignored_retake:
        print(f"\nNotice: {len(ignored_retake)} tab(s) had 'Take the Quiz Again' / retake buttons and were ignored.")

    target_tabs = actionable
    if getattr(args, "tab", None) is not None:
        tab_arg = str(args.tab).strip()
        if tab_arg.isdigit():
            idx = int(tab_arg) - 1
            if 0 <= idx < len(all_pages):
                target_tabs = [all_pages[idx]]
        else:
            filtered = [t for t in actionable if t.get("tab_id") == tab_arg or tab_arg in t.get("url", "")]
            if filtered:
                target_tabs = filtered

    if not target_tabs:
        print("\nNo active or startable quizzes found on any open tab.")
        if ignored_retake:
            print("(Tabs with 'Take the Quiz Again' buttons were ignored as configured.)")
        return

    print(f"\nFound {len(target_tabs)} actionable quiz tab(s):")
    for idx, t in enumerate(target_tabs, 1):
        mode_str = "In progress" if t.get("is_taking") else "Ready to start (Take the Quiz)"
        print(f"  {idx}. {t.get('title', 'Quiz')[:55]} [{mode_str}]")

    # If --start-only flag is set: start all ready tabs and exit
    if getattr(args, "start_only", False):
        print("\n=== Automatically Starting Quizzes on All Ready Tabs (--start-only) ===")
        to_start = [t for t in target_tabs if t.get("can_start")]
        if not to_start:
            print("All target tabs are already started!")
            return
        for idx, t in enumerate(to_start, 1):
            print(f"\n[{idx}/{len(to_start)}] Starting {t.get('title')[:55]}...")
            res = await start_quiz_on_tab(t)
            if res.get("success"):
                print(f"  -> Quiz successfully started! ({res.get('url')})")
            else:
                print(f"  -> Error starting quiz: {res.get('error')}")
        print("\nCompleted starting all ready quizzes.")
        return

    # If --start-all is set: start all ready tabs first before solving
    if getattr(args, "start_all", False):
        print("\n=== Pre-Starting All Ready Quizzes (--start-all) ===")
        for t in target_tabs:
            if t.get("can_start"):
                print(f"Starting {t.get('title')[:55]}...")
                res = await start_quiz_on_tab(t)
                if res.get("success"):
                    print(f"  -> Started successfully.")
                else:
                    print(f"  -> Start failed: {res.get('error')}")

    # Process each quiz tab sequentially: start (if not started) -> solve -> advance
    print("\n" + "=" * 75)
    print(f"  STARTING AUTO-SOLVE PIPELINE FOR {len(target_tabs)} QUIZ TAB(S)")
    print("=" * 75)

    completed_quizzes = 0
    for idx, t in enumerate(target_tabs, 1):
        print(f"\n>>> [QUIZ {idx}/{len(target_tabs)}] {t.get('title')} <<<")
        ws_url = t.get("tab", {}).get("webSocketDebuggerUrl") or t.get("webSocketDebuggerUrl")

        # Automatically start quiz if not yet started
        if t.get("can_start") and not t.get("is_taking"):
            print("  Automatically starting quiz...")
            start_res = await start_quiz_on_tab(t)
            if not start_res.get("success"):
                print(f"  Failed to start quiz: {start_res.get('error')}. Skipping tab.")
                continue
            print("  Quiz started! Preparing solver...")
            await asyncio.sleep(1.0)

        # Connect and solve
        try:
            async with CanvasCDPClient(ws_url) as client:
                try:
                    await client.send_command("Page.bringToFront")
                except Exception:
                    pass
                solve_res = await solve_quiz_on_client(client, t, args)
                if solve_res.get("success"):
                    completed_quizzes += 1
        except Exception as e:
            print(f"  Error solving quiz on tab {idx}: {e}")

    print("\n" + "=" * 75)
    print(f"  AUTO MODE COMPLETED: {completed_quizzes}/{len(target_tabs)} QUIZZES PROCESSED")
    print("=" * 75)


# ---------------------------------------------------------------------------
# HTTP Agent Bridge Server for External Desktop Agents
# ---------------------------------------------------------------------------

async def cmd_serve(args):
    port = args.port
    ctrl = CanvasQuizController()
    page_info = await ctrl.get_page_info()

    async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            line = await reader.readline()
            if not line:
                writer.close()
                await writer.wait_closed()
                return

            request_line = line.decode("utf-8", errors="replace").strip()
            parts = request_line.split(" ")
            if len(parts) < 2:
                writer.close()
                await writer.wait_closed()
                return

            method, path = parts[0], parts[1]
            content_length = 0
            while True:
                header_line = await reader.readline()
                if not header_line or header_line in (b"\r\n", b"\n"):
                    break
                header_str = header_line.decode("utf-8", errors="replace").strip()
                if ":" in header_str:
                    k, v = header_str.split(":", 1)
                    if k.strip().lower() == "content-length":
                        try:
                            content_length = int(v.strip())
                        except ValueError:
                            content_length = 0

            body = {}
            if content_length > 0:
                body_bytes = await reader.readexactly(content_length)
                try:
                    body = json.loads(body_bytes.decode("utf-8"))
                except Exception:
                    body = {}

            if method == "OPTIONS":
                status, resp_data = 200, {"ok": True}
            elif method == "GET" and path == "/api/status":
                data = await ctrl.extract_active_question()
                status, resp_data = 200, {
                    "page_url": page_info.get("url"),
                    "title": page_info.get("title"),
                    "quiz_state": data
                }
            elif method == "GET" and path == "/api/question":
                data = await ctrl.extract_active_question()
                status, resp_data = 200, data
            elif method == "POST" and path == "/api/answer":
                opt = body.get("option", 1)
                auto_next = body.get("next", False)
                delay = body.get("delay", 2.0)
                res = await ctrl.select_answer(option=opt, and_next=auto_next, delay=delay)
                status, resp_data = 200, res
            elif method == "POST" and path == "/api/solve":
                backend = body.get("backend", "auto")
                res = await ctrl.solve_active_question(backend=backend)
                status, resp_data = 200, res
            elif method == "POST" and path == "/api/next":
                res = await ctrl.click_next()
                status, resp_data = 200, res
            elif method == "POST" and path == "/api/step":
                backend = body.get("backend", "auto")
                auto_next = body.get("next", True)
                delay = body.get("delay", 2.0)
                res = await ctrl.step(backend=backend, auto_next=auto_next, delay=delay)
                status, resp_data = 200, res
            elif method == "GET" and path == "/api/tabs":
                data = await ctrl.find_all_quiz_tabs()
                clean_data = {
                    "total_pages": len(data["all_pages"]),
                    "taking": [{"tab_id": t.get("tab_id"), "title": t.get("title"), "url": t.get("url")} for t in data["taking"]],
                    "ready_to_start": [{"tab_id": t.get("tab_id"), "title": t.get("title"), "url": t.get("url"), "start_button": t.get("start_button")} for t in data["ready_to_start"]],
                    "ignored_retake": [{"tab_id": t.get("tab_id"), "title": t.get("title"), "url": t.get("url"), "ignored_button": t.get("ignored_again_button")} for t in data["ignored_retake"]]
                }
                status, resp_data = 200, clean_data
            elif method == "POST" and path == "/api/start":
                started = await ctrl.search_and_start_quizzes()
                status, resp_data = 200, {"started": started}
            else:
                status, resp_data = 404, {"error": "Endpoint not found"}

            payload = json.dumps(resp_data, ensure_ascii=False).encode("utf-8")
            response_headers = (
                f"HTTP/1.1 {status} OK\r\n"
                f"Content-Type: application/json; charset=utf-8\r\n"
                f"Content-Length: {len(payload)}\r\n"
                f"Access-Control-Allow-Origin: *\r\n"
                f"Access-Control-Allow-Methods: GET, POST, OPTIONS\r\n"
                f"Access-Control-Allow-Headers: Content-Type\r\n"
                f"Connection: close\r\n\r\n"
            ).encode("utf-8")

            writer.write(response_headers)
            writer.write(payload)
            await writer.drain()
        except Exception as e:
            err_payload = json.dumps({"error": str(e)}).encode("utf-8")
            writer.write(
                f"HTTP/1.1 500 Internal Server Error\r\nContent-Type: application/json\r\nContent-Length: {len(err_payload)}\r\nConnection: close\r\n\r\n".encode("utf-8")
            )
            writer.write(err_payload)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(handle_client, "127.0.0.1", port)
    print(f"=== Canvas HTTP Agent Bridge running on http://127.0.0.1:{port} ===")
    print("Endpoints:")
    print(f"  GET  http://127.0.0.1:{port}/api/status     - Current quiz & page status")
    print(f"  GET  http://127.0.0.1:{port}/api/tabs       - Scan all open tabs for quizzes")
    print(f"  GET  http://127.0.0.1:{port}/api/question   - Active question & options (JSON)")
    print(f"  POST http://127.0.0.1:{port}/api/start      - Start all ready quizzes (ignore retakes)")
    print(f"  POST http://127.0.0.1:{port}/api/answer     - Select radio option e.g. {{'option': 2, 'next': true}}")
    print(f"  POST http://127.0.0.1:{port}/api/solve      - Solve active question")
    print(f"  POST http://127.0.0.1:{port}/api/step       - Solve + Answer + Click Next")
    print(f"  POST http://127.0.0.1:{port}/api/next       - Advance to next question")

    async with server:
        await server.serve_forever()


# ---------------------------------------------------------------------------
# Main Entry Point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Canvas LMS MCQ Assist Tool")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # status
    subparsers.add_parser("status", help="Check CDP connection and current question status")

    # search
    subparsers.add_parser("search", help="Search all open tabs for quizzes and display status")

    # start
    p_start = subparsers.add_parser("start", help="Search all open tabs, ignore retakes, and start all ready quizzes")
    p_start.add_argument("--tab", help="Target specific tab by number (1..N) or tab ID")

    # extract
    subparsers.add_parser("extract", help="Extract active question and options in JSON")

    # solve
    p_solve = subparsers.add_parser("solve", help="Solve active question via AI model")
    p_solve.add_argument("--backend", default="auto", choices=["auto", "local", "gateway", "gemini"], help="AI solver backend")

    # answer
    p_ans = subparsers.add_parser("answer", help="Select radio button for option")
    p_ans.add_argument("option", help="Option number (1, 2, 3...) or letter (A, B, C, D)")
    p_ans.add_argument("--next", action="store_true", help="Click Next question button after selecting")
    p_ans.add_argument("--delay", type=float, default=0.0, help="Delay in seconds before clicking Next")

    # next
    subparsers.add_parser("next", help="Click the Next question button")

    # step
    p_step = subparsers.add_parser("step", help="Solve, select radio button, and click Next in one step")
    p_step.add_argument("--backend", default="auto", choices=["auto", "local", "gateway", "gemini"])
    p_step.add_argument("--delay", type=float, default=0.0)
    p_step.add_argument("--no-next", action="store_true", help="Do not click Next button")

    # auto
    p_auto = subparsers.add_parser("auto", help="Automated loop through all questions in quiz and across open tabs")
    p_auto.add_argument("--delay", type=float, default=0.0, help="Delay between questions in seconds")
    p_auto.add_argument("--max-questions", type=int, default=100, help="Maximum questions to answer per quiz")
    p_auto.add_argument("--backend", default="auto", choices=["auto", "local", "gateway", "gemini"])
    p_auto.add_argument("--tab", help="Target specific tab by number (1..N) or tab ID")
    p_auto.add_argument("--start-only", action="store_true", help="Only search and start quizzes on all tabs without answering")
    p_auto.add_argument("--start-all", action="store_true", help="Start all ready quizzes upfront before answering")

    # serve
    p_serve = subparsers.add_parser("serve", help="Start HTTP Agent Bridge for external desktop agents")
    p_serve.add_argument("--port", type=int, default=8765, help="HTTP server port")

    args = parser.parse_args()

    cmd_map = {
        "status": cmd_status,
        "search": cmd_search,
        "start": cmd_start,
        "extract": cmd_extract,
        "solve": cmd_solve,
        "answer": cmd_answer,
        "next": cmd_next,
        "step": cmd_step,
        "auto": cmd_auto,
        "serve": cmd_serve
    }

    try:
        asyncio.run(cmd_map[args.command](args))
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
