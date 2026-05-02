from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


VALID_STATUSES = {"NOT_STARTED", "IN_PROGRESS", "BLOCKED", "DONE"}
WORKER_NAMES = ("desktop", "mobile")


@dataclass
class WorkerUpdate:
    agent: str
    status: str
    current_task: str
    completed_items: list[str]
    changed_files: list[str]
    blockers: list[str]
    dependency_on_other_agent: list[str]
    handoff_items: list[str]
    next_step: str


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def next_task(tasks: dict[str, Any], worker: str) -> dict[str, Any] | None:
    shared_ids_done = {
        item["id"]
        for item in tasks.get("shared", [])
        if item.get("status") == "DONE"
    }
    for item in tasks.get(worker, []):
        if item.get("status") != "NOT_STARTED":
            continue
        depends_on = set(item.get("depends_on", []))
        if depends_on.issubset(shared_ids_done):
            return item
    return None


def summarize_state(state: dict[str, Any], tasks: dict[str, Any]) -> str:
    lines = [
        f"Project goal: {state['project_goal']}",
        f"Coordinator cycle: {state['coordinator']['cycle']}",
    ]
    for worker in WORKER_NAMES:
        worker_state = state[worker]
        task_titles = [
            f"{item['id']}={item['status']}"
            for item in tasks.get(worker, [])
        ]
        lines.extend(
            [
                f"{worker} status: {worker_state['status']}",
                f"{worker} current_task: {worker_state['current_task'] or '-'}",
                f"{worker} task_queue: {', '.join(task_titles) if task_titles else '-'}",
                f"{worker} blocked_by: {', '.join(worker_state['blocked_by']) if worker_state['blocked_by'] else '-'}",
            ]
        )
    shared_titles = [
        f"{item['id']}={item['status']}"
        for item in tasks.get("shared", [])
    ]
    lines.extend(
        [
            f"integration status: {state['integration']['status']}",
            f"shared tasks: {', '.join(shared_titles) if shared_titles else '-'}",
        ]
    )
    return "\n".join(lines)


def build_worker_instruction(worker: str, task: dict[str, Any], state: dict[str, Any], tasks: dict[str, Any]) -> str:
    return (
        f"You are receiving a new {worker} task.\n\n"
        f"Task id: {task['id']}\n"
        f"Task title: {task['title']}\n"
        f"Task description: {task['description']}\n\n"
        "Shared project snapshot:\n"
        f"{summarize_state(state, tasks)}\n\n"
        "Return strict JSON only using the agreed schema."
    )


def parse_worker_update(raw: str) -> WorkerUpdate:
    payload = json.loads(raw)
    required = {
        "agent",
        "status",
        "current_task",
        "completed_items",
        "changed_files",
        "blockers",
        "dependency_on_other_agent",
        "handoff_items",
        "next_step",
    }
    missing = required - payload.keys()
    if missing:
        raise ValueError(f"worker response missing keys: {sorted(missing)}")
    if payload["status"] not in VALID_STATUSES:
        raise ValueError(f"invalid worker status: {payload['status']}")
    return WorkerUpdate(**payload)


def mark_shared_contracts(tasks: dict[str, Any], update: WorkerUpdate) -> None:
    if not update.handoff_items:
        return
    for item in tasks.get("shared", []):
        if item["status"] == "DONE":
            continue
        if "contract" in item["id"]:
            item["status"] = "IN_PROGRESS"


def complete_task(tasks: dict[str, Any], worker: str, current_task: str, done: bool) -> None:
    for item in tasks.get(worker, []):
        if item["title"] == current_task or item["id"] == current_task:
            item["status"] = "DONE" if done else "IN_PROGRESS"
            break


def refresh_integration_state(state: dict[str, Any], tasks: dict[str, Any]) -> None:
    shared = tasks.get("shared", [])
    desktop_done = all(item["status"] == "DONE" for item in tasks.get("desktop", []))
    mobile_done = all(item["status"] == "DONE" for item in tasks.get("mobile", []))
    contracts_done = all(item["status"] == "DONE" for item in shared) if shared else True

    state["integration"]["api_contract_ready"] = contracts_done
    state["integration"]["ready_for_joint_test"] = contracts_done and desktop_done and mobile_done

    if state["integration"]["ready_for_joint_test"]:
        state["integration"]["status"] = "DONE"
    elif contracts_done and (desktop_done or mobile_done):
        state["integration"]["status"] = "IN_PROGRESS"
    elif any(worker["status"] == "BLOCKED" for worker in (state["desktop"], state["mobile"])):
        state["integration"]["status"] = "BLOCKED"
    else:
        state["integration"]["status"] = "NOT_STARTED"


def apply_update(state: dict[str, Any], tasks: dict[str, Any], update: WorkerUpdate) -> None:
    worker_state = state[update.agent]
    worker_state["status"] = update.status
    worker_state["current_task"] = update.current_task
    worker_state["done_items"] = update.completed_items
    worker_state["changed_files"] = update.changed_files
    worker_state["blocked_by"] = update.blockers
    worker_state["next_step"] = update.next_step
    worker_state["handoff_items"] = update.handoff_items

    complete_task(tasks, update.agent, update.current_task, update.status == "DONE")
    mark_shared_contracts(tasks, update)

    if update.status == "DONE" and update.handoff_items:
        state["integration"]["open_items"].extend(update.handoff_items)
    if update.status == "BLOCKED":
        state["integration"]["risks"].extend(update.blockers)

    refresh_integration_state(state, tasks)


def choose_actions(state: dict[str, Any], tasks: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    actions: list[tuple[str, dict[str, Any]]] = []
    for worker in WORKER_NAMES:
        if state[worker]["status"] == "IN_PROGRESS":
            continue
        task = next_task(tasks, worker)
        if task:
            actions.append((worker, task))
    return actions


def default_mock_response(worker: str, task: dict[str, Any]) -> str:
    return json.dumps(
        {
            "agent": worker,
            "status": "IN_PROGRESS",
            "current_task": task["title"],
            "completed_items": [],
            "changed_files": [],
            "blockers": [],
            "dependency_on_other_agent": task.get("depends_on", []),
            "handoff_items": [],
            "next_step": f"Continue working on {task['title']}",
        },
        ensure_ascii=False,
    )


def run_agent_command(worker: str, prompt_text: str, task: dict[str, Any], command_template: str | None) -> str:
    if not command_template:
        return default_mock_response(worker, task)

    command = command_template.format(worker=worker)
    completed = subprocess.run(
        command,
        input=prompt_text,
        text=True,
        capture_output=True,
        shell=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"{worker} command failed with exit code {completed.returncode}: {completed.stderr.strip()}"
        )
    return completed.stdout.strip()


def run_cycle(state_path: Path, tasks_path: Path, command_template: str | None) -> bool:
    state = load_json(state_path)
    tasks = load_json(tasks_path)
    state_before = deepcopy(state)

    state["coordinator"]["cycle"] += 1
    state["coordinator"]["last_actions"] = []

    actions = choose_actions(state, tasks)
    if not actions:
        state["last_updated"] = utc_now()
        save_json(state_path, state)
        return state != state_before

    for worker, task in actions:
        prompt_text = build_worker_instruction(worker, task, state, tasks)
        raw = run_agent_command(worker, prompt_text, task, command_template)
        update = parse_worker_update(raw)
        apply_update(state, tasks, update)
        state["coordinator"]["last_actions"].append(
            {
                "worker": worker,
                "task_id": task["id"],
                "worker_status": update.status,
                "timestamp": utc_now(),
            }
        )

    state["last_updated"] = utc_now()
    save_json(state_path, state)
    save_json(tasks_path, tasks)
    return state != state_before


def main() -> int:
    parser = argparse.ArgumentParser(description="Minimal multi-agent coordinator loop")
    parser.add_argument("--state", required=True, type=Path, help="Path to project_state.json")
    parser.add_argument("--tasks", required=True, type=Path, help="Path to tasks.json")
    parser.add_argument("--worker-command", default=None, help="Command template used to invoke workers. Use {worker} as a placeholder.")
    parser.add_argument("--interval", type=int, default=30, help="Polling interval in seconds")
    parser.add_argument("--once", action="store_true", help="Run a single cycle and exit")
    args = parser.parse_args()

    if args.once:
        changed = run_cycle(args.state, args.tasks, args.worker_command)
        print("updated" if changed else "no-op")
        return 0

    while True:
        try:
            changed = run_cycle(args.state, args.tasks, args.worker_command)
            print(f"[{utc_now()}] {'updated' if changed else 'no-op'}")
        except Exception as exc:  # pragma: no cover
            print(f"[{utc_now()}] coordinator error: {exc}", file=sys.stderr)
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
