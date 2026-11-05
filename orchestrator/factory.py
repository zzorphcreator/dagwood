"""Agent factory: builds worker agents and the supervisor from node specs."""

from __future__ import annotations

import datetime
import os

from dotenv import load_dotenv
from openai import AsyncOpenAI
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
from pydantic_ai.providers.openai import OpenAIProvider

from .catalog import BUILTIN, Catalog, split_id
from .documents import FORMATS, DocumentSpec, publisher_instructions
from .models import DEFAULT_MODEL, TIER_EFFORT, get_info
from .schema import DAGNode, DAGPlan
from .tools import REGISTRY, NodeDeps

load_dotenv()

_models: dict[str, OpenAIChatModel] = {}
DEEPINFRA_URL = "https://api.deepinfra.com/v1/openai"


def openai_client(http_client=None) -> AsyncOpenAI:
    """DeepInfra client. DeepInfra answers 429 "Model busy" (engine_overloaded) for seconds at a time,
    which failed whole steps at the SDK's default of 2 quick retries. Each request now retries up to
    ORCHESTRATOR_MODEL_MAX_RETRIES times (default 8; backoff 0.5s doubling to 8s, or the server's
    Retry-After), riding out roughly 40s of overload. Retrying one request repeats no earlier work."""
    try:
        retries = max(0, int(os.environ.get("ORCHESTRATOR_MODEL_MAX_RETRIES", "8")))
    except ValueError:
        retries = 8
    extra = {"http_client": http_client} if http_client is not None else {}
    return AsyncOpenAI(base_url=DEEPINFRA_URL, api_key=os.environ["DEEPINFRA_API_KEY"], max_retries=retries, **extra)


def get_model(model_id: str | None = None):
    """A DeepInfra-hosted chat model (cached per id). Needs DEEPINFRA_API_KEY."""
    model_id = model_id or DEFAULT_MODEL
    if model_id not in _models:
        _models[model_id] = OpenAIChatModel(model_id, provider=OpenAIProvider(openai_client=openai_client()))
    return _models[model_id]


BASE_INSTRUCTIONS = (
    "You are one step in a multi-agent workflow. "
    "Do only your assigned goal and be concise."
)


def _today() -> str:
    return datetime.date.today().isoformat()


def worker_instructions(node: DAGNode) -> str:
    return (
        f"{BASE_INSTRUCTIONS}\n"
        f"Today's date is {_today()}. Your training data may be out of date: for anything time-sensitive "
        "(prices, market data, news, recent events), use your tools to get current information and say "
        "where it came from. If you have no tool for that, state that the figures may be outdated rather "
        "than presenting them as current.\n"
        f"Role: {node.role}\n{node.instructions}"
    ).strip()


def worker_settings(tier: str = "standard") -> OpenAIChatModelSettings:
    """DeepSeek on DeepInfra only emits reasoning (shown as node "thoughts") when an effort is set.

    The effort follows the agent's tier (TIER_EFFORT: heavy agents think harder). Setting
    ORCHESTRATOR_REASONING_EFFORT (low / medium / high) forces one effort for every agent;
    setting it empty disables reasoning.
    """
    effort = os.environ.get("ORCHESTRATOR_REASONING_EFFORT")
    if effort is None:
        effort = TIER_EFFORT.get(tier, "low")
    return OpenAIChatModelSettings(openai_reasoning_effort=effort) if effort else OpenAIChatModelSettings()


def build_worker(node: DAGNode, model=None) -> Agent:
    instructions = worker_instructions(node)
    if node.deliverable:  # a publishing step returns a DocumentSpec, rendered to a file by the executor
        instructions = f"{instructions}\n{publisher_instructions(node.deliverable)}"
    agent: Agent = Agent(
        model or get_model(),
        instructions=instructions,
        deps_type=NodeDeps,
        model_settings=worker_settings(node.tier),
        output_type=DocumentSpec if node.deliverable else str,
        retries={"output": 2} if node.deliverable else 1,
    )
    # Built-in tools attach here; external toolkits are opened per run (see Catalog.toolsets_for).
    for toolkit_id in node.tools:
        source, name = split_id(toolkit_id)
        if source != BUILTIN:
            continue
        if name not in REGISTRY:
            raise ValueError(f"unknown tool {name!r} for node {node.id!r}")
        agent.tool(REGISTRY[name][0])
    return agent


DELIVER_OPTIONS = ("auto", "none", "pdf", "slides", "pptx")


def _deliverable_rule(deliver: str) -> str:
    if deliver == "auto":
        return (
            "- deliverable: leave empty unless the user asks for a file. If the task asks for a presentation, "
            "deck or slides, add one final publishing step with deliverable \"slides\" (a modern web slide deck); "
            "for PowerPoint or pptx use \"pptx\"; for a PDF, report document or printable brief use \"pdf\". "
            "The publishing step has no tools, depends on the steps whose results it presents, and nothing "
            "depends on it; earlier steps gather and write the content. Never ask the user which format.\n"
        )
    if deliver == "none":
        return "- deliverable: always leave deliverable empty; the user wants a text answer.\n"
    return (
        f"- deliverable: always leave deliverable empty. The user wants the result as a {FORMATS[deliver][2]}; a "
        "final publishing step that turns your last steps' results into it is added automatically, so plan the "
        "steps that produce the content.\n"
    )


def apply_delivery(plan: DAGPlan, deliver: str) -> DAGPlan:
    """Make the plan match the user's "deliver as" choice: auto keeps the planner's publishing steps,
    none strips them, and a format replaces them with one publishing step after every final step."""
    if deliver == "auto":
        return plan
    nodes = [n.model_copy(update={"deliverable": None}) if n.deliverable else n for n in plan.nodes]
    if deliver == "none":
        return plan.model_copy(update={"nodes": nodes})
    if any(n.deliverable for n in plan.nodes):  # the planner added one anyway: keep it, in the chosen format
        nodes = [n.model_copy(update={"deliverable": deliver}) if n.deliverable else n for n in plan.nodes]
        return plan.model_copy(update={"nodes": nodes})
    depended = {d for n in nodes for d in n.depends_on}
    ids = {n.id for n in nodes}
    node_id = next(i for i in ("publish", *(f"publish_{k}" for k in range(2, 100))) if i not in ids)
    publish = DAGNode(
        id=node_id,
        role="worker",
        goal=f"Present the results as a {FORMATS[deliver][2]}",
        depends_on=[n.id for n in nodes if n.id not in depended],
        deliverable=deliver,
    )
    return plan.model_copy(update={"nodes": [*nodes, publish]})


def supervisor_prompt(catalog: Catalog, deliver: str = "auto") -> str:
    tools = "\n".join(f"- {t.id}: {t.description}" for t in catalog.enabled())
    return (
        "You are the supervisor of a multi-agent workflow engine.\n"
        "Given a user task, decompose it into a DAG of steps.\n"
        "Rules:\n"
        "- ids: short slugs (letters, digits, _ or -).\n"
        "- depends_on lists ids that must finish first. No cycles. Each step only sees the outputs of the "
        "steps listed in its depends_on, so any step that analyses, combines, summarises or builds on earlier "
        "results must list those steps; leave it empty only for steps that need nothing from the others.\n"
        "- role: researcher, coder, critic, or worker.\n"
        "- goal: one concrete deliverable for that step.\n"
        "- instructions: extra guidance for the worker (may be empty).\n"
        "- tools: ids from the list below (may be empty); give each step only what it needs:\n"
        f"{tools}\n"
        "- tier: how hard the step is, which picks the model that runs it. light = simple extraction, "
        "formatting or lookups; standard = ordinary research or writing (the default); heavy = short but "
        "hard reasoning, analysis or synthesis. Steps with search or web toolkits should be light or "
        "standard: they read large results, so a stronger model costs much more there. Use heavy sparingly.\n"
        "- model: always leave empty.\n"
        f"{_deliverable_rule(deliver)}"
        f"- Today's date is {_today()}. Your knowledge and the workers' may be out of date. Any step that "
        "needs current or real-time information (prices, market data, news, recent events, anything "
        "'latest' or 'now') must be given a search or web toolkit from the list above, if one exists.\n"
        "- Keep it small: 2-6 nodes unless the task clearly needs more.\n"
        "Return only the plan."
    )


def plan_problems(plan: DAGPlan, catalog: Catalog) -> list[str]:
    """Everything wrong with a plan, as messages a planner (or API caller) can act on."""
    if not plan.nodes:
        return ["a plan needs at least one node"]
    problems: list[str] = []
    try:
        plan.order()
    except ValueError as e:
        problems.append(str(e))
    for node in plan.nodes:
        unknown = catalog.unknown(node.tools)
        if unknown:
            problems.append(f"node {node.id!r} uses unknown tools: {', '.join(unknown)}")
        if node.model and get_info(node.model) is None:
            problems.append(f"node {node.id!r} uses unknown model {node.model!r}; see GET /models")
        if node.deliverable:
            if node.tools:
                problems.append(f"publishing step {node.id!r} must have no tools")
            if any(node.id in other.depends_on for other in plan.nodes):
                problems.append(f"publishing step {node.id!r} must be a final step: nothing may depend on it")
    return problems


def build_supervisor(catalog: Catalog, model=None, deliver: str = "auto") -> Agent:
    agent: Agent = Agent(
        model or get_model(),
        instructions=supervisor_prompt(catalog, deliver),
        output_type=DAGPlan,
        retries={"output": 3},  # room for the planner to correct invented tool ids
    )

    @agent.output_validator
    def check_plan(ctx: RunContext[None], plan: DAGPlan) -> DAGPlan:
        # The planner picks tiers; models come from the run's mode or a reviewer's override.
        plan = plan.model_copy(update={"nodes": [n.model_copy(update={"model": None}) for n in plan.nodes]})
        problems = plan_problems(plan, catalog)
        # A flat multi-step plan usually means a forgotten depends_on (a synthesis step that would see
        # nothing). Ask once; a plan that really is all-parallel passes the second time.
        if not problems and len(plan.nodes) >= 3 and not plan.edges() and ctx.retry == 0:
            problems.append(
                "no step depends on another, so no step will see any other step's output. If a later step "
                "uses earlier results (analysis, synthesis, summary), add those ids to its depends_on. If every "
                "step is truly independent, return the same plan again"
            )
        if problems:
            raise ModelRetry("Fix these problems and return the plan again: " + "; ".join(problems))
        return plan

    return agent
