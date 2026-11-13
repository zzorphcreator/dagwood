// Plain-language explanation of what the app does, shown on the empty canvas and in a dialog.
import { X } from 'lucide-react';

const STEPS = [
  {
    title: 'You describe a task',
    body: 'Type a question or a job, for example “Compare Bitcoin and Ethereum prices this week”.',
  },
  {
    title: 'You can steer the team (optional)',
    body: 'Next to the task box, “Agents” asks for a team size and “Agent types” for the kinds of agents you want, for example “researcher, critic” or your own, like “fact checker”. These are hints: the planner follows them where the task allows. Submit the same task with different hints and compare the plans.',
  },
  {
    title: 'A planner designs a team of agents',
    body: 'An AI planner splits the task into steps. Each step becomes an agent (a box on the graph) with its own goal and the tools it may use, such as web search or a calculator. Arrows show which agents need another agent’s results first. The plan is checked automatically, and a plan with problems goes back to the planner to fix.',
  },
  {
    title: 'You review the plan (optional)',
    body: 'With “Review plan first” switched on, nothing runs until you approve. You can change any agent’s goal or tools, delete agents, check the cost estimate, and connect any accounts the tools need.',
  },
  {
    title: 'The agents do the work',
    body: 'An agent starts as soon as everything it depends on is finished, so independent agents run side by side (a few at a time). Each agent receives its parents’ results as input. Every agent has a limit on tool calls and tokens, so cost stays bounded.',
  },
  {
    title: 'Tools fetch real information',
    body: 'Agents with web search look up current facts instead of relying on the model’s memory, which can be out of date. Only toolkits enabled under “Tools” are available.',
  },
  {
    title: 'You can inspect every step',
    body: 'Click a box to see what the agent received, its reasoning, the tools it called, its answer and its cost. Click an arrow to see exactly what was passed along it. You can stop a run at any time; finished agents keep their results.',
  },
];

const LEGEND = [
  { cls: 'pending', label: 'waiting' },
  { cls: 'running', label: 'running' },
  { cls: 'succeeded', label: 'done' },
  { cls: 'failed', label: 'failed' },
  { cls: 'skipped', label: 'skipped (an earlier step failed or the run was stopped)' },
];

function MiniGraph() {
  return (
    <div className="mini-graph" aria-hidden="true">
      <span className="mg-box task">Your task</span>
      <span className="mg-arrow">→</span>
      <span className="mg-box planner">Planner</span>
      <span className="mg-arrow">→</span>
      <span className="mg-stack">
        <span className="mg-box agent">Research A</span>
        <span className="mg-box agent">Research B</span>
      </span>
      <span className="mg-arrow">→</span>
      <span className="mg-box agent">Compare</span>
      <span className="mg-arrow">→</span>
      <span className="mg-box answer">Answer</span>
    </div>
  );
}

export function HowItWorksContent() {
  return (
    <div className="how-it-works">
      <h2>How it works</h2>
      <p className="hiw-lede">
        Instead of one AI answering everything at once, this app builds a small <strong>team of AI agents</strong> for
        your task, each with one job, and shows you the team as a graph.
      </p>
      <MiniGraph />
      <p className="hiw-purpose">
        <strong>Why this exists.</strong> Dagwood is a learning tool, not a product. It is built to show how agents
        hand work to each other and what the <strong>harness</strong> around them does: the checks that send a bad plan
        back to the planner, the limits on tools and tokens, the points where a person can step in, and what each step
        costs. Run small tasks, click everything, and compare what you see with the code.
      </p>
      <ol className="hiw-steps">
        {STEPS.map((s, i) => (
          <li key={s.title}>
            <span className="hiw-num">{i + 1}</span>
            <div>
              <strong>{s.title}</strong>
              <p>{s.body}</p>
            </div>
          </li>
        ))}
      </ol>
      <p className="hiw-note">
        <strong>About costs.</strong> Every cost here is an estimate: the tokens used, multiplied by model prices saved
        in the code on a fixed date. Providers change prices often and retire models, so check your provider’s
        dashboard for what you were actually charged, and update the prices in <code>orchestrator/models.py</code> when
        they drift. Tool calls through Composio are not included.
      </p>
      <div className="hiw-legend">
        <strong>Box colours:</strong>
        {LEGEND.map((l) => (
          <span key={l.cls} className="hiw-legend-item">
            <span className={`hiw-swatch ${l.cls}`} /> {l.label}
          </span>
        ))}
      </div>
    </div>
  );
}

export default function HowItWorksDialog({ onClose }: { onClose: () => void }) {
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" role="dialog" aria-label="How it works" onClick={(e) => e.stopPropagation()}>
        <button className="ghost modal-close" onClick={onClose} aria-label="Close">
          <X size={18} />
        </button>
        <HowItWorksContent />
      </div>
    </div>
  );
}
