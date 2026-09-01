# Setting a credential

**Short answer:** you probably don't need one yet.

```bash
pyligent-agents setup      # what the library can see, and what will happen
```

---

## Most of this works with no key at all

Every test, demo, eval and benchmark runs against a deterministic backend that
executes in-process. Not a mock — a second implementation of the same `LLMClient`
contract, which is why turn caps, retries and idempotency are testable at all.

```bash
pytest                                          # over 440 tests, no credential
python examples/run.py demo all                 # all four layers
python bench/run.py --corpus bench/corpus-sec   # 97 real filings, scored
evidence-check document.pdf extraction.json     # no model, no network
```

If you are evaluating this project, do that first. It costs nothing, needs no
account, and exercises the parts worth judging. A credential only buys you real
model output, which is the least novel thing here.

---

## When you do want a real model

Get a key from [console.anthropic.com/settings/keys](https://console.anthropic.com/settings/keys),
then export it in your shell:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."          # bash, zsh
```

```fish
set -Ux ANTHROPIC_API_KEY "sk-ant-..."         # fish
```

```powershell
setx ANTHROPIC_API_KEY "sk-ant-..."            # Windows; opens in NEW terminals
```

To keep it across sessions, put the line in your shell profile — `~/.zshrc`,
`~/.bashrc`, or `~/.config/fish/config.fish`. `pyligent-agents setup` prints the
right one for the shell you are actually in.

Then confirm the library agrees with you:

```bash
pyligent-agents setup
#   credential         found in ANTHROPIC_API_KEY
```

**The value is never printed** — by `setup`, by `doctor`, or by any error message.
They report which variable holds it, never what it contains, so their output is safe
to paste into an issue.

---

## The `.env` trap

**This library does not read `.env`.** Only the benchmark scripts under `bench/` do,
and only because they were written for a different job.

A key sitting in an unread `.env` looks exactly like no key at all, and the failure
arrives later, somewhere unrelated. If you keep keys in a file, export them first:

```bash
set -a; source .env; set +a
```

And make sure git cannot see it — `pyligent-agents setup` checks this for you and
says so if `.env` is not ignored.

---

## Verifying it actually works

```bash
PYLIGENT_LIVE_MODEL=1 pytest -m live -s
```

One real call on a short document — **about a cent** — asserting that the reply
parses into the shape the pipeline consumes and that every quote it cites is
genuinely in the source. It is a contract test, not an accuracy test.

Live tests are opt-in twice over, by marker *and* environment variable, so having a
key exported never causes a spend you did not ask for.

---

## Identity-linked keys

If your key is identity-linked, every request must name the workspace it acts in, and
the API returns a `400` that does not say so. Find the id in the console under
Settings → Workspaces:

```bash
export ANTHROPIC_WORKSPACE_ID="wrkspc_..."
```

The benchmark translates that specific `400` into this instruction rather than
letting the raw error through.

---

## What a credential costs you

Every run is capped before it starts — turns, dollars and wall-clock, all visible in
`pyligent-agents doctor`. Defaults are 12 turns, $2.00, 600 seconds. The governor
bills against usage the API reports, never an estimate.

Two things worth knowing before you point this at anything real:

- **Document text leaves your process** in the request body when a real backend is
  in use. `doctor` states this under *Data residency*. Check it against your data
  policy before pointing this at agreements you did not author.
- **Tools are not sandboxed.** They run in your process with your privileges.

---

## In CI

Store the key as a repository secret, never in the workflow file:

```bash
gh secret set ANTHROPIC_API_KEY -R your-org/your-repo
```

The `live-model` job in `ci.yml` is written to skip itself when no secret is
configured, so forks and contributors see it neutral rather than failing. That
matters more than it sounds: a job that fails for people who cannot possibly fix it
trains everyone to ignore red.

---

## If you commit a key

**Rotate it first.** Immediately, before anything else.

Rewriting history does not un-leak a credential — it was readable for however long it
was there, and unreachable git objects stay fetchable by SHA until the host garbage-
collects them. The only action that actually revokes access is revoking the key.

`load()` in `pyligent_agents.config_file` refuses a configuration file that contains
something shaped like a credential, precisely so this happens less often. Config
holds the *name* of a variable, never its value:

```yaml
extractor:
  provider: anthropic
  model: claude-sonnet-5
  api_key_env: ANTHROPIC_API_KEY     # the NAME, never the value
```
