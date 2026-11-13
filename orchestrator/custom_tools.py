"""Your own tools: start here.

A tool is a plain Python function an agent may call. Three things turn a function into a tool:

  1. Typed parameters. The model sees their names and types and fills them in.
  2. A docstring. It is the description the model reads to decide when and how to call the tool,
     so say what the tool returns and what each argument means.
  3. An entry in CUSTOM_TOOLS at the bottom of this file. Its one-line description is what the
     *planner* reads when it decides which agents get the tool, and what the Tools drawer shows.

Restart the backend and the tool is live: it is listed under Tools, the planner can assign it,
and you can add or remove it per agent when you review a plan.

Rules of thumb:
  - Return a short string. Whatever you return goes into the agent's context and costs tokens.
  - Return errors as text ("error: unknown topic ...") instead of raising: the agent can then
    read the message and try again.
  - Tools run inside the server's event loop. Anything slow or blocking (HTTP calls, big files)
    should be an `async def` using an async client, or it stalls every running agent.
  - A tool runs with your machine's permissions and is called with arguments chosen by a model
    that may have read untrusted web pages. Validate arguments; never pass them to a shell,
    `eval`, or a file path without checking them (see how `calc` in tools.py does it).

`team_handbook` below is a working placeholder. It answers from a tiny made-up fact sheet, which
is the most common reason to write a tool: giving agents information the model cannot know.
Try it with a task like "When is our next deploy window and who do I ask about access?", then
replace HANDBOOK with your own data, or the whole function with your own tool.
"""

from __future__ import annotations

# Made-up example data. Replace it with your own (or load it from a file or database).
HANDBOOK = {
    "deploys": "Production deploys happen on Tuesdays and Thursdays at 14:00 UTC. No deploys on Fridays.",
    "on-call": "On-call rotates weekly, handing over on Mondays at 09:00 UTC. The schedule is in the #ops channel.",
    "access": "Request system access from the platform team; requests are reviewed within two working days.",
    "holidays": "The team is closed from 24 December to 1 January.",
}


def team_handbook(topic: str) -> str:
    """Look up one topic in the team handbook and return what it says.

    Args:
        topic: one of the handbook's topics, e.g. "deploys". Pass "topics" to list them all.
    """
    key = topic.strip().lower()
    if key in ("topics", "topic", ""):
        return f"Topics: {', '.join(sorted(HANDBOOK))}"
    if key in HANDBOOK:
        return HANDBOOK[key]
    matches = [name for name in HANDBOOK if key and (key in name or name in key)]
    if len(matches) == 1:
        return HANDBOOK[matches[0]]
    return f"error: no handbook entry for {topic!r}. Topics: {', '.join(sorted(HANDBOOK))}"


# name -> (function, one-line description for the planner and the Tools drawer)
CUSTOM_TOOLS = {
    "team_handbook": (
        team_handbook,
        "Example custom tool: looks up a team's internal handbook (deploy schedule, on-call, access "
        f"requests, holidays; topics: {', '.join(sorted(HANDBOOK))}). Only for questions about that team.",
    ),
}
