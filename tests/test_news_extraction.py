"""The `claude -p` extraction client and the identity check that guards what it produces.

No subprocess is ever spawned and no model is ever called: the CLI is faked at the
`asyncio.create_subprocess_exec` boundary, so what is pinned is our handling of what comes back -
envelopes, code fences, malformed shapes, timeouts - rather than the model's judgement.

The validation half is the more important one. `comparable_name` decides whether an extracted fact
reaches the facts layer at all, and it has to be forgiving in exactly one direction: a transliterated
spelling is the same player, a different name is not. Both live runs of this pipeline produced one
case of each, and they are the two tests to keep.
"""
import asyncio
import json

import pytest

from src.fpl.client import claude_cli
from src.fpl.client.claude_cli import ClaudeCliClient, ClaudeCliError, _parse_json
from src.fpl.loader.news.validate import comparable_name


SCHEMA = {
    'type': 'array',
    'items': {
        'type': 'object',
        'required': ['player_id', 'web_name', 'fact', 'form', 'availability'],
    },
}


class FakeProcess:
    """Stands in for the CLI: yields one canned stdout/exit code, or hangs when asked."""

    def __init__(self, stdout=b'', stderr=b'', returncode=0, hang=False):
        self._stdout, self._stderr = stdout, stderr
        self.returncode = returncode
        self._hang = hang
        self.killed = False

    async def communicate(self, _input=None):
        if self._hang:
            await asyncio.sleep(3600)
        return self._stdout, self._stderr

    def kill(self):
        self.killed = True

    async def wait(self):
        return self.returncode


def envelope(result, is_error=False, subtype='success'):
    return json.dumps({
        'result': result, 'is_error': is_error, 'subtype': subtype,
        'num_turns': 1, 'total_cost_usd': 0.01,
    }).encode()


@pytest.fixture
def client():
    return ClaudeCliClient(executable='/bin/true')


def fake_cli(monkeypatch, *processes):
    """Serve `processes` in order, so retry behaviour can be tested."""
    calls = {'n': 0}
    queue = list(processes)

    async def _spawn(*_args, **_kwargs):
        calls['n'] += 1
        return queue.pop(0) if len(queue) > 1 else queue[0]

    monkeypatch.setattr(asyncio, 'create_subprocess_exec', _spawn)
    return calls


FACTS = '[{"player_id": 1, "web_name": "A", "fact": "f", "form": 0.1, "availability": 0.0}]'


def test_a_clean_reply_is_parsed(client, monkeypatch):
    fake_cli(monkeypatch, FakeProcess(stdout=envelope(FACTS)))
    result = asyncio.run(client.generate_content('p', response_schema=SCHEMA))
    assert result == [{'player_id': 1, 'web_name': 'A', 'fact': 'f', 'form': 0.1,
                       'availability': 0.0}]


def test_code_fences_are_stripped(client, monkeypatch):
    """Models wrap JSON in ```json fences often enough that failing on it would be flaky."""
    fake_cli(monkeypatch, FakeProcess(stdout=envelope(f'```json\n{FACTS}\n```')))
    assert asyncio.run(client.generate_content('p', response_schema=SCHEMA))[0]['player_id'] == 1


def test_an_unparseable_reply_is_retried_then_raises(client, monkeypatch):
    calls = fake_cli(monkeypatch, FakeProcess(stdout=envelope('sorry, I cannot help')))
    with pytest.raises(ClaudeCliError, match='failed'):
        asyncio.run(client.generate_content('p', response_schema=SCHEMA, retries=2, base_delay=0))
    assert calls['n'] == 3, 'one attempt plus two retries'


def test_a_reply_missing_a_required_key_is_rejected(client, monkeypatch):
    """A plausible-looking object with a key missing would surface as a KeyError much later."""
    fake_cli(monkeypatch, FakeProcess(stdout=envelope('[{"player_id": 1, "web_name": "A"}]')))
    with pytest.raises(ClaudeCliError):
        asyncio.run(client.generate_content('p', response_schema=SCHEMA, retries=0, base_delay=0))


def test_a_recoverable_failure_then_success(client, monkeypatch):
    fake_cli(monkeypatch,
             FakeProcess(stdout=b'not json at all', returncode=1),
             FakeProcess(stdout=envelope(FACTS)))
    assert asyncio.run(
        client.generate_content('p', response_schema=SCHEMA, retries=1, base_delay=0))


def test_a_cli_error_envelope_raises(client, monkeypatch):
    """`is_error` is the CLI telling us it refused or failed; the text is not an extraction."""
    fake_cli(monkeypatch, FakeProcess(stdout=envelope('I will not do that', is_error=True)))
    with pytest.raises(ClaudeCliError, match='reported an error'):
        asyncio.run(client.generate_content('p', response_schema=SCHEMA, retries=0, base_delay=0))


def test_a_timeout_kills_the_process_and_raises(monkeypatch):
    client = ClaudeCliClient(executable='/bin/true', timeout=0.01)
    process = FakeProcess(hang=True)
    fake_cli(monkeypatch, process)
    with pytest.raises(ClaudeCliError, match='failed'):
        asyncio.run(client.generate_content('p', response_schema=SCHEMA, retries=0, base_delay=0))
    assert process.killed, 'a hung CLI must not be left running'


def test_a_missing_cli_is_a_clear_error(monkeypatch):
    """On a machine without Claude Code the message must name the install, not a PATH traceback."""
    monkeypatch.setattr(claude_cli.shutil, 'which', lambda _name: None)
    with pytest.raises(ClaudeCliError, match='not on PATH'):
        ClaudeCliClient()


def test_tools_are_disabled_and_the_repo_is_not_the_working_directory(client, monkeypatch):
    """`claude -p` started inside this repo reads CLAUDE.md and behaves like an engineering agent.

    The first live trial refused to extract, citing this repo's own no-fabrication rule. So the
    call must be isolated: an empty cwd and no tools.
    """
    seen = {}

    async def _spawn(*args, **kwargs):
        seen['argv'] = args
        seen['cwd'] = kwargs.get('cwd')
        return FakeProcess(stdout=envelope(FACTS))

    monkeypatch.setattr(asyncio, 'create_subprocess_exec', _spawn)
    asyncio.run(client.generate_content('p', response_schema=SCHEMA))
    argv = list(seen['argv'])
    assert '--disallowed-tools' in argv and 'Bash' in argv[argv.index('--disallowed-tools') + 1]
    assert '--append-system-prompt' in argv
    assert seen['cwd'] and not seen['cwd'].startswith(claude_cli.os.getcwd())
    client.close()


def test_the_schema_is_stated_in_the_prompt(client, monkeypatch):
    """The CLI has no server-side structured-output mode, so the schema has to be in the text."""
    seen = {}

    async def _spawn(*_args, **_kwargs):
        return FakeProcess(stdout=envelope(FACTS))

    monkeypatch.setattr(asyncio, 'create_subprocess_exec', _spawn)
    original = ClaudeCliClient._run

    async def _capture(self, prompt):
        seen['prompt'] = prompt
        return await original(self, prompt)

    monkeypatch.setattr(ClaudeCliClient, '_run', _capture)
    asyncio.run(client.generate_content('the article', response_schema=SCHEMA))
    assert 'availability' in seen['prompt'] and 'JSON Schema' in seen['prompt']


def test_a_bare_prompt_returns_text(client, monkeypatch):
    fake_cli(monkeypatch, FakeProcess(stdout=envelope('just words')))
    assert asyncio.run(client.generate_content('p')) == 'just words'


@pytest.mark.parametrize('extracted, official', [
    ('Odegaard', 'Ødegaard'),      # halted the first live validation run
    ('Muharemovic', 'Muharemović'),
    ('Gross', 'Groß'),
    ('Nunez', 'Núñez'),
    ("N'Dicka", 'NDicka'),
])
def test_a_transliterated_spelling_is_the_same_player(extracted, official):
    assert comparable_name(extracted) == comparable_name(official)


@pytest.mark.parametrize('extracted, official', [
    ('Murillo', 'Aina'),           # a real mis-mapping, caught on the first live run
    ('Haaland', 'Gabriel'),
    ('Son', 'Sonne'),
])
def test_a_different_name_is_a_different_player(extracted, official):
    """The check exists to catch a fact filed under the wrong id. It must still do that."""
    assert comparable_name(extracted) != comparable_name(official)


def test_parse_json_rejects_a_non_array_when_an_array_is_promised():
    with pytest.raises(ValueError, match='expected a JSON array'):
        _parse_json('{"player_id": 1}', SCHEMA)
