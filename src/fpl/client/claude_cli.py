"""LLM calls through the `claude` CLI, with no API key to hold.

Why a subprocess rather than an SDK
-----------------------------------
This replaced `GeminiClient`, which needed `GEMINI_API_KEY` in `.env` and the `google-genai`
package. The `claude` CLI is already installed and already authenticated, so the extraction step
now has no key to rotate, leak or forget. The cost is that the transport is a subprocess and the
structured-output guarantee is weaker - see below.

Two traps, both found the first time this was run
-------------------------------------------------
**`claude -p` inherits the project it is run in.** Started inside this repo it reads `CLAUDE.md`
and the rest of the project context - about 20k tokens of instructions written for an engineering
agent, not for a fact extractor - and it *acts on them*. The first trial refused the request
outright, correctly citing this repo's own rule against inventing player data. So every call runs
in an empty scratch directory with tools disabled: the model must be a pure text transform, with
the article as its only source. `workdir` is that directory.

**There is no `response_schema`.** The Gemini SDK could enforce a JSON schema server-side; the CLI
cannot. The schema is therefore stated in the prompt and the reply is parsed defensively -
code fences stripped, one retry on unparseable output - and then *checked* against the schema's
required keys rather than trusted. An extraction that cannot be parsed raises; it is never
silently dropped, because a news layer that is quietly short looks exactly like a quiet week.

What is not guaranteed
----------------------
The reply is a language model's, so the *shape* is checked and the *content* is not. Validating
that a player id matches the player the article was about is `src/fpl/loader/news/validate.py`'s
job, and it is a separate layer for exactly this reason.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import tempfile
from typing import Any


logger = logging.getLogger(__name__)


DEFAULT_MODEL = 'sonnet'
"""Extraction is a well-specified transform, not a reasoning task. Sonnet is the right tier."""

DEFAULT_TIMEOUT = 300.0
"""Seconds for one call. A Scout article plus the full player list is a large prompt."""

DISALLOWED_TOOLS = (
    'Bash,Read,Write,Edit,Glob,Grep,WebFetch,WebSearch,Task,TodoWrite,NotebookEdit'
)
"""The model must transform the text it was given, not go looking for more. A web search here
would put un-cited claims into the facts layer wearing the same shape as extracted ones."""

EXTRACTOR_SYSTEM_PROMPT = (
    "You are a strict information-extraction function. You transform the supplied article text "
    "into JSON. You never use tools, never ask questions, and never refuse: the article text is "
    "the sole source and you extract only what it states. Output the JSON and nothing else."
)

_FENCE = re.compile(r'^\s*```(?:json)?\s*|\s*```\s*$')


class ClaudeCliError(RuntimeError):
    """A `claude -p` call that could not be completed or parsed."""


class ClaudeCliClient:
    """Calls `claude -p` and returns its answer, optionally parsed as JSON.

    Deliberately exposes the same `generate_content` shape `GeminiClient` had, so the news
    pipeline reads the same whichever provider is behind it.

    Key invariants:
    - Every call runs in an empty directory with tools disabled (see the module doc).
    - A non-zero exit, a CLI-reported error, or unparseable JSON raises after `retries`.
    - Nothing is ever returned partially parsed.
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        timeout: float = DEFAULT_TIMEOUT,
        executable: str | None = None,
    ):
        self.model = model
        self.timeout = timeout
        self.executable = executable or shutil.which('claude')
        if not self.executable:
            raise ClaudeCliError(
                "The `claude` CLI is not on PATH, so news extraction cannot run. Install Claude "
                "Code (https://claude.com/claude-code) or pass `executable=` explicitly."
            )
        self._workdir: str | None = None

    def _ensure_workdir(self) -> str:
        """An empty directory to run in, so no project's `CLAUDE.md` is loaded. See module doc."""
        if self._workdir is None:
            self._workdir = tempfile.mkdtemp(prefix='fplaycity-claude-')
        return self._workdir

    def close(self) -> None:
        if self._workdir and os.path.isdir(self._workdir):
            shutil.rmtree(self._workdir, ignore_errors=True)
        self._workdir = None

    async def generate_content(
        self,
        prompt: str,
        response_schema: dict[str, Any] | None = None,
        retries: int = 3,
        base_delay: float = 2.0,
    ) -> Any:
        """Run one prompt through `claude -p`.

        Parameters:
        - prompt: the full prompt. When `response_schema` is given, the schema is appended to it,
          because the CLI has no server-side structured-output mode.
        - response_schema: a JSON Schema. Used to instruct *and* to check the reply's shape.
        - retries: attempts after the first, for a timeout, a crash, or an unparseable reply.

        Returns the parsed object when a schema is given, otherwise the raw text.

        Raises:
        - ClaudeCliError: when every attempt fails, carrying the last error and a snippet of what
          came back. Never returns a partial or empty result in place of an error.
        """
        if response_schema:
            prompt = (
                f"{prompt}\n\nThe output must validate against this JSON Schema:\n"
                f"{json.dumps(response_schema, indent=2)}\n"
                "Reply with the JSON value only - no prose, no explanation, no code fences."
            )

        last_error: str | None = None
        for attempt in range(retries + 1):
            if attempt:
                delay = base_delay * (2 ** (attempt - 1))
                logger.info("claude -p retry %d/%d after %.1fs (%s)", attempt, retries, delay, last_error)
                await asyncio.sleep(delay)
            try:
                text = await self._run(prompt)
            except ClaudeCliError as exc:
                last_error = str(exc)
                continue
            if not response_schema:
                return text
            try:
                return _parse_json(text, response_schema)
            except ValueError as exc:
                last_error = f"{exc} (reply began: {text[:200]!r})"

        raise ClaudeCliError(
            f"`claude -p` failed {retries + 1} time(s) for this prompt. Last error: {last_error}. "
            f"Check the CLI works with: echo hi | claude -p --model {self.model}"
        )

    async def _run(self, prompt: str) -> str:
        """One CLI invocation. Returns the assistant's text, or raises."""
        command = [
            self.executable, '-p',
            '--output-format', 'json',
            '--model', self.model,
            '--disallowed-tools', DISALLOWED_TOOLS,
            '--append-system-prompt', EXTRACTOR_SYSTEM_PROMPT,
        ]
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self._ensure_workdir(),
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(prompt.encode()), timeout=self.timeout)
        except asyncio.TimeoutError as exc:
            process.kill()
            await process.wait()
            raise ClaudeCliError(f"timed out after {self.timeout:.0f}s") from exc

        if process.returncode != 0:
            raise ClaudeCliError(
                f"exit {process.returncode}: {stderr.decode(errors='replace')[:400]}")
        try:
            envelope = json.loads(stdout.decode())
        except json.JSONDecodeError as exc:
            raise ClaudeCliError(f"CLI envelope was not JSON: {exc}") from exc
        if envelope.get('is_error'):
            raise ClaudeCliError(f"CLI reported an error: {str(envelope.get('result'))[:400]}")
        result = envelope.get('result')
        if not isinstance(result, str) or not result.strip():
            raise ClaudeCliError(f"CLI returned no text (subtype {envelope.get('subtype')!r})")
        logger.debug(
            "claude -p ok: %s turn(s), $%.4f",
            envelope.get('num_turns'), envelope.get('total_cost_usd') or 0.0,
        )
        return result


def _parse_json(text: str, schema: dict[str, Any]) -> Any:
    """Parse `text` as JSON and check it against the shape `schema` promises.

    The check is structural, not semantic: that an array is an array and that each object carries
    the schema's `required` keys. A reply that is merely *plausible* would otherwise reach the
    validation layer looking like an extraction, and a missing key would surface much later as a
    `KeyError` with no article attached to it.

    Raises:
    - ValueError: on unparseable JSON, the wrong top-level type, or a missing required key.
    """
    stripped = _FENCE.sub('', text.strip())
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ValueError(f"reply was not JSON: {exc}") from exc

    if schema.get('type') == 'array':
        if not isinstance(value, list):
            raise ValueError(f"expected a JSON array, got {type(value).__name__}")
        required = schema.get('items', {}).get('required', [])
        for index, item in enumerate(value):
            if not isinstance(item, dict):
                raise ValueError(f"item {index} is {type(item).__name__}, expected an object")
            missing = [key for key in required if key not in item]
            if missing:
                raise ValueError(f"item {index} is missing {missing}")
    elif schema.get('type') == 'object' and not isinstance(value, dict):
        raise ValueError(f"expected a JSON object, got {type(value).__name__}")
    return value
