# UI

React + React Flow front end for the orchestrator. See the [project README](../README.md) for setup.

```bash
npm install
npm run dev      # http://localhost:5173, proxies API calls to the backend on :8000
npm run build    # type-check and build to dist/
```

Main pieces: `Graph.tsx` (dagre layout of the plan DAG), `ApprovalBar.tsx` / `PlanEditor.tsx`
(review and edit a plan before it runs), `NodePanel.tsx` + `LiveFeed.tsx` (an agent's prompt,
streamed output, thoughts and tool calls), `Routing.tsx` (cost mode / model picker).
