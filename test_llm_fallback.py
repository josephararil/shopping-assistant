"""
Offline verification of the Gemini reasoning-model fallback chain.

Google returned sustained HTTP 503 "This model is currently experiencing high demand" for
gemini-pro-latest on 2026-07-31, which killed Stage 3 and stalled Stage 4 — the run produced
nothing while each stage ground through its retry ladder. Exponential backoff cannot fix a
capacity outage, so common._reason_with_fallback walks config.GEMINI_REASONING_FALLBACKS.

These tests replace requests.post with a scripted fake, so nothing here touches the network
and no API key is needed. common._RETRY_DELAYS is zeroed so the ladder does not really sleep.

Run: python test_llm_fallback.py
"""

import sys
import types

import config as C
import common as X

_failures = []


def chk(name, cond, detail=""):
    if cond:
        print(f"{name} [OK]")
    else:
        print(f"{name} [FAIL] {detail}")
        _failures.append(name)


class FakeResponse:
    """Minimal stand-in for requests.Response covering what _gemini touches."""

    def __init__(self, status_code, text_payload=None):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._payload = text_payload or "{}"
        self.text = self._payload

    def json(self):
        return {"candidates": [{"content": {"parts": [{"text": self._payload}]},
                                "finishReason": "STOP"}]}

    def raise_for_status(self):
        if not self.ok:
            raise X.requests.exceptions.HTTPError(f"{self.status_code} Server Error")


def install_fake_post(script):
    """script: model-substring -> FakeResponse or an Exception instance to raise.
    Records every model actually called, in order."""
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        model = url.split("/models/")[1].split(":")[0]
        calls.append(model)
        outcome = script.get(model)
        if isinstance(outcome, BaseException):
            raise outcome
        if outcome is None:
            return FakeResponse(200, '{"ok": true}')
        return outcome

    X.requests.post = fake_post
    return calls


_orig_post = X.requests.post
_orig_delays = X._RETRY_DELAYS
X._RETRY_DELAYS = [0, 0, 0]

MSGS = [{"role": "user", "content": "hello"}]

try:
    # ── 1. The reported incident: pro is 503, flash answers. ──────────────────
    X.MODEL_FALLBACKS_USED.clear()
    calls = install_fake_post({
        "gemini-pro-latest": FakeResponse(503, '{"error":{"code":503}}'),
        "gemini-flash-latest": FakeResponse(200, '{"offers": []}'),
    })
    out = X._gemini(MSGS, "claude-sonnet-4-6", 1000, want_search=False)
    chk("a 503 on gemini-pro-latest falls back to gemini-flash-latest",
        out == '{"offers": []}', f"got {out!r}")
    chk("the fallback is recorded for last_run.json",
        X.MODEL_FALLBACKS_USED == ["gemini-pro-latest->gemini-flash-latest"],
        f"got {X.MODEL_FALLBACKS_USED!r}")
    # The primary must not burn the full ladder when a fallback is waiting: 4 attempts at a
    # 180 s read timeout is ~12 minutes against a 20-minute job cap.
    n_primary = calls.count("gemini-pro-latest")
    chk("the primary fails over fast rather than burning the full retry ladder",
        n_primary == C.GEMINI_ATTEMPTS_BEFORE_FALLBACK < X._MAX_RETRIES,
        f"called primary {n_primary}x, expected {C.GEMINI_ATTEMPTS_BEFORE_FALLBACK}")

    # ── 2. A network timeout must fall back too, not just an HTTP status. ─────
    X.MODEL_FALLBACKS_USED.clear()
    install_fake_post({
        "gemini-pro-latest": X.requests.exceptions.ReadTimeout("read timed out"),
        "gemini-flash-latest": FakeResponse(200, '{"ok": 1}'),
    })
    out = X._gemini(MSGS, "claude-sonnet-4-6", 1000, want_search=False)
    chk("a ReadTimeout on the primary also falls back", out == '{"ok": 1}', f"got {out!r}")

    # ── 3. A 400 must NOT fall back: it fails identically everywhere, and hiding it ──
    #      behind a fallback disguises our own bad request as Google's capacity problem.
    X.MODEL_FALLBACKS_USED.clear()
    calls = install_fake_post({
        "gemini-pro-latest": FakeResponse(400, '{"error":{"message":"bad responseSchema"}}'),
        "gemini-flash-latest": FakeResponse(200, '{"ok": 1}'),
    })
    try:
        X._gemini(MSGS, "claude-sonnet-4-6", 1000, want_search=False)
        chk("a 400 raises instead of falling back", False, "no exception raised")
    except X.requests.exceptions.HTTPError:
        chk("a 400 raises instead of falling back", True)
    chk("a 400 never reaches the fallback model",
        "gemini-flash-latest" not in calls, f"calls were {calls}")
    chk("a 400 is not recorded as a fallback", X.MODEL_FALLBACKS_USED == [],
        f"got {X.MODEL_FALLBACKS_USED!r}")

    # ── 4. Whole chain down -> raise, so the stage's own try/except reports it. ───
    X.MODEL_FALLBACKS_USED.clear()
    install_fake_post({
        "gemini-pro-latest": FakeResponse(503),
        "gemini-flash-latest": FakeResponse(503),
    })
    try:
        X._gemini(MSGS, "claude-sonnet-4-6", 1000, want_search=False)
        chk("an exhausted chain raises", False, "no exception raised")
    except X.requests.exceptions.HTTPError:
        chk("an exhausted chain raises", True)

    # ── 5. The happy path must not record a fallback or call a second model. ──
    X.MODEL_FALLBACKS_USED.clear()
    calls = install_fake_post({"gemini-pro-latest": FakeResponse(200, '{"ok": 1}')})
    out = X._gemini(MSGS, "claude-sonnet-4-6", 1000, want_search=False)
    chk("a healthy primary is used alone", calls == ["gemini-pro-latest"], f"calls {calls}")
    chk("a healthy run records no fallback", X.MODEL_FALLBACKS_USED == [],
        f"got {X.MODEL_FALLBACKS_USED!r}")

    # ── 6. A model with no configured fallback keeps the FULL ladder. ─────────
    #      Trimming attempts is only justified when somewhere better exists to go.
    X.MODEL_FALLBACKS_USED.clear()
    calls = install_fake_post({"gemini-flash-latest": FakeResponse(503)})
    try:
        X._gemini(MSGS, "claude-haiku-4-5-20251001", 1000, want_search=False)
    except X.requests.exceptions.HTTPError:
        pass
    chk("a model with no fallback still spends the full retry ladder",
        calls.count("gemini-flash-latest") == X._MAX_RETRIES,
        f"called {calls.count('gemini-flash-latest')}x, expected {X._MAX_RETRIES}")

    # ── 7. Every mapped reasoning model resolves to a real chain entry. ───────
    for canonical, gmodel in C.GEMINI_MODEL_MAP.items():
        for fb in C.GEMINI_REASONING_FALLBACKS.get(gmodel, []):
            chk(f"fallback {fb!r} for {gmodel!r} is not the model itself", fb != gmodel)
    chk("the search model is never used as a reasoning fallback",
        all(C.GEMINI_SEARCH_MODEL not in v
            for v in C.GEMINI_REASONING_FALLBACKS.values()),
        "the lite search tier is not a reasoning substitute")

finally:
    X.requests.post = _orig_post
    X._RETRY_DELAYS = _orig_delays
    X.MODEL_FALLBACKS_USED.clear()

if _failures:
    print(f"\n{len(_failures)} failure(s): {_failures}")
    sys.exit(1)
print("\nAll assertions passed.")
