# Multi-Agent Coordinator Scaffold

This directory contains a minimal scaffold for running:

- one `coordinator` agent
- one `desktop` execution agent
- one `mobile` execution agent

The coordinator is intended to:

- maintain shared project state
- assign the next task to each execution agent
- collect structured JSON responses
- detect blockers and integration readiness

## Files

- `coordinator.py`: orchestration loop
- `project_state.example.json`: shared state template
- `tasks.example.json`: task queue template
- `prompts/coordinator_prompt.txt`: coordinator system prompt
- `prompts/desktop_prompt.txt`: desktop agent system prompt
- `prompts/mobile_prompt.txt`: mobile agent system prompt

## How It Works

1. Copy `project_state.example.json` to `project_state.json`.
2. Copy `tasks.example.json` to `tasks.json`.
3. Fill in your project goal and task list.
4. Adapt `coordinator.py` so `run_agent_command()` calls your real CLI agents.
5. Start the loop:

```powershell
python docs/multi_agent/coordinator.py --state docs/multi_agent/project_state.json --tasks docs/multi_agent/tasks.json --once
```

Remove `--once` if you want a polling loop.

## Response Contract

Each execution agent must return strict JSON like:

```json
{
  "agent": "desktop",
  "status": "IN_PROGRESS",
  "current_task": "Implement login form",
  "completed_items": ["Scaffolded auth screen"],
  "changed_files": ["src/auth/login.tsx"],
  "blockers": [],
  "dependency_on_other_agent": [],
  "handoff_items": ["Need final token response schema"],
  "next_step": "Wire submit flow to auth API"
}
```

Allowed `status` values:

- `NOT_STARTED`
- `IN_PROGRESS`
- `BLOCKED`
- `DONE`

## Important Limitation

This scaffold does not magically connect separate CLI windows. To automate coordination, the coordinator must be the process that invokes the worker agents itself, or all agents must read and write the same state files in a controlled way.

## Integration Options

You can adapt `run_agent_command()` in `coordinator.py` to any of these:

- a local CLI agent command
- an API call to an LLM service
- a job queue worker
- a wrapper script that launches a dedicated terminal session

The coordinator logic is separated from the transport so you can swap that layer without rewriting the state model.
