# A note to you, the investigator dev agent

Hi — before you dive in, a heads-up and an ask.

We've just started piloting a new way of working: a **Context Graph** that acts as
this project's shared memory. It already holds investigator's modules, commit
history, and architecture (we backfilled them), and it's meant to help you **not
rebuild things that already exist**, understand *why* past decisions were made, and
record your own decisions so they aren't lost between sessions. Your quick guide is
`cg/AGENT_CG_GUIDE.md`.

**This is brand new — literally day one.** We wired it up hours ago and you're the
first agent to actually use it on a real project. So please expect rough edges, and
please be patient with them. Nothing here is set in stone; the whole point of this
run is to find out what works and what doesn't.

**Here's the real ask: help us validate the methodology.** As you work:

- **Use it naturally** — query before building, record decisions worth remembering,
  advance tasks and open change requests through the actions. Treat it as a teammate,
  not a chore.
- **Tell us when it gets in your way or falls short.** Concretely, flag anything like:
  - a tool or endpoint that errors, hangs, or returns something confusing;
  - a query that *should* have found existing code/decisions but came back empty or wrong;
  - a `FLAG` or a `409` block that felt incorrect or unhelpful;
  - an action that's missing, awkward, or named wrong for how you actually think;
  - the guide (`AGENT_CG_GUIDE.md`) being unclear or telling you the wrong thing.
- **Be specific and honest.** "The reuse check flagged X but Y already existed and it
  didn't surface it" is gold. Don't smooth over friction to be polite — friction is
  exactly the data we need.

Keep a short running list of these as you go (a `cg/FEEDBACK.md` is perfect), or just
call them out inline. We'd rather hear ten small annoyances than none.

Thanks for helping us shake this down. Build well — and grumble freely.

— the team
