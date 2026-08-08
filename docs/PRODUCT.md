# Checkpoint — Product Notes

## Positioning
Checkpoint is the **human authority surface** for a fleet of AI agents.
Every agent that needs a human decision uses the same gateway. The human sees
one clean screen, not 18 inboxes.

## Target user
Operators who run multiple autonomous agents (Hermes, Codex, custom scripts)
and need a fast, structured way to approve or reject decisions without context
switching.

## Key insight
Agents already know how to ask questions (they use LLMs). What they don't have
is a shared, reliable interface that turns a human's answer into structured data
the agent can act on. Checkpoint fills that gap with a PWA your phone already
has — Safari.

## Competitive landscape
- **Slack/Teams bots:** chat-based, threading is fragile, no structured answer
- **Email:** slow, no live status, no push
- **PagerDuty / OpsGenie:** overkill, ops-only vocabulary
- **Custom per-agent UIs:** sprawl, the human checks N inboxes

Checkpoint's advantage is simplicity: one shared API, one PWA, one tap.

## Monetization (if applicable)
Not a commercial product. Personal infrastructure. Could be extended for teams
with per-user keys and audit logs.
