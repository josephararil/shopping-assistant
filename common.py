"""Shared helpers for the diamond-finder pipeline."""

import os, json, ssl, smtplib, datetime as dt, time
from email.message import EmailMessage
import requests
import config as C

# ---------------------------- LLM provider ----------------------------

PROVIDER          = os.environ.get("LLM_PROVIDER", "anthropic").strip().lower()
GEMINI_API_KEY    = os.environ.get("GEMINI_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

STATE_DIR = "state"

_RETRY_STATUSES = {429, 500, 502, 503, 504}
_MAX_RETRIES = 4
_RETRY_DELAYS = [2, 4, 8]  # seconds between attempts


def _post_with_retry(url, headers, json_body, timeout=180):
    """POST with exponential backoff on transient errors (5xx, 429, network).
    Auth failures (401, 403) and client errors (400, 422) are returned immediately."""
    for attempt in range(_MAX_RETRIES):
        try:
            r = requests.post(url, headers=headers, json=json_body, timeout=timeout)
            if r.status_code not in _RETRY_STATUSES or attempt == _MAX_RETRIES - 1:
                return r
            delay = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
            print(f"  [retry {attempt + 1}/{_MAX_RETRIES}] HTTP {r.status_code}, retrying in {delay}s")
            time.sleep(delay)
        except requests.exceptions.RequestException as exc:
            if attempt == _MAX_RETRIES - 1:
                raise
            delay = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
            print(f"  [retry {attempt + 1}/{_MAX_RETRIES}] {type(exc).__name__}: {exc}, retrying in {delay}s")
            time.sleep(delay)


def resolved_provider(provider=None):
    """The provider that will actually handle a call: the explicit arg if given,
    else the global LLM_PROVIDER. Lets callers tailor prompts per provider."""
    return (provider or PROVIDER).strip().lower()


def llm(messages, model, max_tokens=2000, want_search=False, response_schema=None,
        provider=None, search_prompt=None):
    """Single entry point for all LLM calls. Returns plain text.
    messages is a list of {"role", "content"} dicts with string content.
    response_schema: Gemini only — JSON Schema dict added as response_format.
    search_prompt: Gemini only — a dedicated prompt for the split-out search step
      (see _gemini). None falls back to wrapping the stage text in a generic directive.
    provider overrides the global LLM_PROVIDER for this call; None uses the global."""
    p = (provider or PROVIDER).strip().lower()
    if p == "gemini":
        return _gemini(messages, model, max_tokens, want_search, response_schema, search_prompt)
    return _anthropic(messages, model, max_tokens, want_search)


def _anthropic(messages, model, max_tokens, want_search):
    body = {"model": model, "max_tokens": max_tokens, "messages": messages}
    if want_search:
        body["tools"] = [{"type": "web_search_20250305", "name": "web_search",
                          "max_uses": C.WEB_SEARCH_MAX_USES}]
    r = _post_with_retry("https://api.anthropic.com/v1/messages",
        headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json_body=body)
    r.raise_for_status()
    return "".join(b.get("text", "") for b in r.json().get("content", [])
                   if b.get("type") == "text").strip()


def _gemini_extract(r):
    """Return (text, finish_reason) from a Gemini generateContent response.
    finish_reason != "STOP" (e.g. "MAX_TOKENS") signals the output was cut off —
    on thinking models, hidden reasoning tokens can exhaust maxOutputTokens before
    the visible answer completes, which would otherwise look like an empty result."""
    cand = (r.json().get("candidates") or [{}])[0]
    parts = [p["text"] for p in cand.get("content", {}).get("parts", []) if "text" in p]
    return "".join(parts).strip(), cand.get("finishReason")


def _gemini_search(search_text, max_tokens):
    """Run live web-search grounding on GEMINI_SEARCH_MODEL (the lite tier, the only
    one that survives Google's grounding gateway). Sends search_text verbatim with the
    google_search tool and returns the grounded text. Returns "" on any failure —
    the caller then reasons knowledge-only."""
    smodel = C.GEMINI_SEARCH_MODEL
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{smodel}:generateContent"
    headers = {"x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"}
    body = {
        "contents": [{"role": "user", "parts": [{"text": search_text}]}],
        "generationConfig": {"maxOutputTokens": max_tokens},
        "tools": [{"google_search": {}}],
    }
    print(f"  [gemini] Search via {smodel} (google_search)")
    try:
        r = _post_with_retry(url, headers=headers, json_body=body)
    except requests.exceptions.RequestException as exc:
        print(f"  [gemini] search {type(exc).__name__}; reasoning knowledge-only")
        return ""
    if not r.ok:
        print(f"  [gemini] search HTTP {r.status_code}; reasoning knowledge-only")
        return ""
    out, finish = _gemini_extract(r)
    if finish and finish != "STOP":
        print(f"  [gemini] WARNING: search finishReason={finish} — grounding may be truncated")
    print(f"  [gemini] Search returned {len(out)} chars of grounding")
    return out


def _gemini(messages, model, max_tokens, want_search, response_schema=None, search_prompt=None):
    gmodel = C.GEMINI_MODEL_MAP.get(model, "gemini-flash-latest")
    text = "\n\n".join(m["content"] for m in messages)

    # Search and reasoning are split: grounding runs on the lite search model (above),
    # then the flagship reasoning model runs tools-free with the grounding injected as
    # context. Flagship + google_search times out on Google's grounding gateway, and
    # this also keeps responseSchema off the search call (they conflict).
    # The search step uses a dedicated lead-generation prompt when the caller supplies
    # one (Stage 1 Find); otherwise it wraps the stage text in a generic search
    # directive (Stage 3 Verify). The grounded leads are framed for the reasoner by
    # SEARCH_RESULTS_PREAMBLE (.replace, so leads containing braces are safe).
    if want_search:
        if search_prompt is not None:
            search_text = search_prompt
        else:
            search_text = (
                "Search the web for current, concrete facts relevant to the task below: "
                "real prices, dates, availability, named hotels/destinations, and sources. "
                "Return a thorough list of findings. Do not write final analysis or JSON.\n\n"
                "TASK:\n" + text)
        grounding = _gemini_search(search_text, max_tokens)
        if grounding:
            text = C.SEARCH_RESULTS_PREAMBLE.replace("{leads}", grounding) + text

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{gmodel}:generateContent"
    headers = {"x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"}
    print(f"  [gemini] Reasoning via {gmodel} (schema={response_schema is not None})")

    body = {
        "contents": [{"role": "user", "parts": [{"text": text}]}],
        "generationConfig": {"maxOutputTokens": max_tokens},
    }
    if response_schema is not None:
        body["generationConfig"]["responseMimeType"] = "application/json"
        body["generationConfig"]["responseSchema"] = response_schema

    r = _post_with_retry(url, headers=headers, json_body=body)
    if not r.ok:
        print(f"  [gemini error] HTTP {r.status_code}: {r.text[:1000]}")
    r.raise_for_status()
    res, finish = _gemini_extract(r)
    if finish and finish != "STOP":
        print(f"  [gemini] WARNING: reasoning finishReason={finish} — output likely truncated "
              f"(raise max_tokens for this stage); a partial/empty parse will look like a quiet day")
    print(f"  [gemini] Parsed response length: {len(res)} chars")
    return res


# ------------------------------ Email ------------------------------

def send_email(subject, html, text):
    """Send a plain + HTML email. SMTP_HOST/USER/PASS must be set in env."""
    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ["SMTP_USER"]
    pw   = os.environ["SMTP_PASS"]
    to   = os.environ.get("EMAIL_TO", user)
    frm  = os.environ.get("EMAIL_FROM", user)
    msg  = EmailMessage()
    msg["Subject"] = subject
    msg["From"]    = frm
    msg["To"]      = to
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    with smtplib.SMTP(host, port) as s:
        s.starttls(context=ssl.create_default_context())
        s.login(user, pw)
        s.send_message(msg)


# ------------------------------ State ------------------------------

def parse_json_block(text):
    """Strip markdown fences and parse the outermost JSON value the model returned,
    choosing object vs array by whichever bracket appears first."""
    t = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    starts = [(t.find(c), c) for c in ("[", "{") if t.find(c) != -1]
    if not starts:
        return None
    _, open_c = min(starts)
    close_c = "]" if open_c == "[" else "}"
    i, j = t.find(open_c), t.rfind(close_c)
    if i != -1 and j != -1 and j > i:
        try:
            return json.loads(t[i:j + 1])
        except json.JSONDecodeError:
            return None
    return None


def load_json(name, default):
    try:
        with open(os.path.join(STATE_DIR, name), encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(name, data):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(os.path.join(STATE_DIR, name), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def today_iso():
    return dt.date.today().isoformat()
