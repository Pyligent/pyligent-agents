# Security policy

## Reporting a vulnerability

Please do **not** open a public issue. Use GitHub's private vulnerability
reporting on this repository, or email the maintainers listed in
`CONTRIBUTING.md`. We aim to acknowledge within three working days.

## What Pyligent Agents does and does not protect against

Being precise about this matters more than a reassuring paragraph.

### Pyligent Agents provides

**Capability boundaries.** `PermissionTier.RESTRICTED` tools are denied unless
an approver explicitly allows them, and `ToolRegistry.clone()` produces
registries in which dangerous tools *do not exist*. This is the primary defence
and the one to rely on.

**Untrusted-content defanging.** `defang_untrusted_content` neutralises
instruction-shaped text in tool results registered `trusted=False`. This is a
**second** line of defence. Pattern filters can be evaded; a subagent with no
dangerous tool in its registry cannot be talked into using one.

**Secret redaction from context.** `redact_secrets` keeps credential-shaped
strings out of the transcript, which is persisted, replayed every turn and
folded into compaction summaries.

**Spend and time limits** that raise rather than warn.

### Pyligent Agents does not provide

- **Sandboxing.** Your tool functions run in your process with your privileges.
  If a tool shells out, Pyligent Agents cannot contain it.
- **Guaranteed prompt-injection immunity.** No pattern filter can promise that.
  Design so that a successful injection reaches nothing worth reaching.
- **Authentication or authorisation.** The approver callback is where you plug
  in your own; Pyligent Agents has no opinion about identity.
- **PII detection or data-residency controls.**
- **Model-output safety filtering.**

### If you are building something regulated

Three habits carry most of the weight:

1. Keep every consequential number in deterministic, tested code. No model
   output should ever be a monetary figure.
2. Give document-reading agents a registry containing *no* restricted tools.
3. Put an idempotency key derived from the facts of the action on every external
   side effect, and let the database enforce uniqueness.
