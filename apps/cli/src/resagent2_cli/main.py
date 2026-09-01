"""Minimal command-line adapter over the existing ResAgent2 interfaces."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, Sequence
from uuid import uuid4

from resagent2_contracts import (
    DatasetRef,
    EnvironmentSpec,
    ResearchRequest,
    RunBudget,
    RunStatus,
    UserAnswer,
    WorkspaceSourceKind,
    WorkspaceSpec,
)
from resagent2_orchestrator import JsonRunStore, ResearchRun

from .composition import CliApplication, build_application


EXIT_COMPLETED = 0
EXIT_FAILED = 1
EXIT_PAUSED = 3
EXIT_RUNNING = 4

ApplicationBuilder = Callable[..., CliApplication]
StoreFactory = Callable[[Path], JsonRunStore]


def _default_data_root() -> str:
    return os.environ.get("RESAGENT2_DATA_ROOT", ".resagent2/data")


def _new_run_id() -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    return f"run_{stamp}_{uuid4().hex[:8]}"


def _assignment(value: str, *, label: str) -> tuple[str, str]:
    if "=" not in value:
        raise ValueError(f"{label} must use NAME=VALUE syntax")
    name, assigned = value.split("=", 1)
    if not name.strip() or not assigned.strip():
        raise ValueError(f"{label} must contain a non-empty name and value")
    return name.strip(), assigned.strip()


def _goal(args: argparse.Namespace) -> str:
    if args.goal is not None:
        return args.goal
    return Path(args.goal_file).expanduser().read_text(encoding="utf-8")


def _workspace_args(parser: argparse.ArgumentParser) -> None:
    sources = parser.add_mutually_exclusive_group()
    sources.add_argument("--workspace", help="existing local workspace directory")
    sources.add_argument("--git", help="Git repository URL to clone into the Run")
    parser.add_argument("--python-version", help="hard Python major.minor constraint")


def _workspace_specs(args: argparse.Namespace) -> dict[str, WorkspaceSpec]:
    environment = (
        EnvironmentSpec(python_version=args.python_version)
        if args.python_version
        else None
    )
    if args.workspace:
        root = Path(args.workspace).expanduser().resolve()
        if not root.is_dir():
            raise ValueError(f"workspace is not a directory: {root}")
        return {
            "ws_main": WorkspaceSpec(
                workspace_id="ws_main",
                source_kind=WorkspaceSourceKind.LOCAL,
                location=str(root),
                environment=environment,
            )
        }
    if args.git:
        return {
            "ws_main": WorkspaceSpec(
                workspace_id="ws_main",
                source_kind=WorkspaceSourceKind.GIT,
                location=args.git,
                environment=environment,
            )
        }
    if args.python_version:
        raise ValueError("--python-version requires --workspace or --git")
    return {}


def _specs_for_existing_run(
    args: argparse.Namespace,
    run: ResearchRun,
) -> dict[str, WorkspaceSpec]:
    supplied = _workspace_specs(args)
    persisted = {
        workspace_id: record.source
        for workspace_id, record in run.workspaces.items()
    }
    if supplied and persisted:
        if set(supplied) != set(persisted):
            raise ValueError("supplied workspace does not match the persisted Run workspace")
        for workspace_id, proposed in supplied.items():
            existing = persisted[workspace_id]
            if (
                proposed.source_kind != existing.source_kind
                or proposed.location != existing.location
                or (
                    proposed.environment is not None
                    and proposed.environment != existing.environment
                )
            ):
                raise ValueError(
                    "supplied workspace does not match the persisted Run workspace"
                )
        return persisted
    return supplied or persisted


def _run_store(data_root: Path) -> JsonRunStore:
    return JsonRunStore(data_root / "state")


def _exit_code(run: ResearchRun) -> int:
    if run.status == RunStatus.COMPLETED:
        return EXIT_COMPLETED
    if run.status == RunStatus.PAUSED:
        return EXIT_PAUSED
    if run.status == RunStatus.RUNNING:
        return EXIT_RUNNING
    return EXIT_FAILED


def _render_run(run: ResearchRun) -> None:
    print(f"Run: {run.run_id}")
    print(f"Status: {run.status.value}")
    print(f"Goal: {run.request.goal}")
    print(f"LLM calls: {run.llm_calls_used}/{run.request.budget.max_llm_calls}")
    if run.latest_scientific_assessment is not None:
        print("Scientific assessment:")
        print(f"  {run.latest_scientific_assessment.statement}")
    if run.workflow is not None:
        print("Tasks:")
        for task in run.workflow.tasks:
            suffix = f" ({len(task.attempts)} attempt(s))" if task.attempts else ""
            print(f"  {task.id}: {task.status.value}{suffix}")
            if task.attempts:
                latest = task.attempts[-1]
                if latest.summary:
                    print(f"    {latest.summary}")
                if latest.error is not None:
                    print(
                        f"    {latest.error.code.value}: {latest.error.message}"
                    )
    if run.pending_question is not None:
        print("Pending question:")
        print(f"  {run.pending_question.text}")
        if run.pending_question.requested_fields:
            fields = ", ".join(run.pending_question.requested_fields)
            print(f"  Fields: {fields}")
    if run.artifacts:
        print("Artifacts:")
        for artifact in run.artifacts.values():
            print(
                f"  {artifact.id}: {artifact.kind} "
                f"[{artifact.producer.value}] {artifact.uri}"
            )
    if run.final_opinion is not None:
        print("Final opinion:")
        print(f"  Verdict: {run.final_opinion.verdict.value}")
        print(f"  {run.final_opinion.statement}")
    if run.completion_violations:
        print("Completion violations:")
        for violation in run.completion_violations:
            print(f"  {violation.code.value}: {violation.message}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="resagent2",
        description="Thin command-line entrypoint for the existing ResAgent2 system.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="create and execute a ResearchRun")
    goals = run.add_mutually_exclusive_group(required=True)
    goals.add_argument("--goal", help="research goal text, passed through unchanged")
    goals.add_argument("--goal-file", help="UTF-8 file containing the research goal")
    run.add_argument("--run-id", default=None)
    run.add_argument("--data-root", default=_default_data_root())
    run.add_argument("--hypothesis")
    run.add_argument("--context", default="")
    run.add_argument("--constraint", action="append", default=[])
    run.add_argument("--dataset", action="append", default=[], metavar="ID=PATH")
    run.add_argument("--max-tasks", type=int, default=8)
    run.add_argument("--max-attempts", type=int, default=2)
    run.add_argument("--max-llm-calls", type=int, default=200)
    run.add_argument("--timeout-seconds", type=int, default=7200)
    _workspace_args(run)

    show = subparsers.add_parser("show", help="show one persisted ResearchRun")
    show.add_argument("run_id")
    show.add_argument("--data-root", default=_default_data_root())

    answer = subparsers.add_parser("answer", help="answer the current pending question")
    answer.add_argument("run_id")
    answer.add_argument("--field", action="append", required=True, metavar="NAME=VALUE")
    answer.add_argument("--data-root", default=_default_data_root())
    _workspace_args(answer)

    resume = subparsers.add_parser("resume", help="continue a persisted running Run")
    resume.add_argument("run_id")
    resume.add_argument("--data-root", default=_default_data_root())
    _workspace_args(resume)
    return parser


def cli(
    argv: Sequence[str] | None = None,
    *,
    application_builder: ApplicationBuilder = build_application,
    store_factory: StoreFactory = _run_store,
) -> int:
    args = _parser().parse_args(argv)
    data_root = Path(args.data_root).expanduser().resolve()

    if args.command == "show":
        _render_run(store_factory(data_root).load(args.run_id))
        return EXIT_COMPLETED

    if args.command == "run":
        datasets = [
            DatasetRef(dataset_id=name, relative_path=path)
            for name, path in (
                _assignment(value, label="--dataset") for value in args.dataset
            )
        ]
        request = ResearchRequest(
            goal=_goal(args),
            hypothesis=args.hypothesis,
            context=args.context,
            constraints=args.constraint,
            dataset_refs=datasets,
            budget=RunBudget(
                max_tasks=args.max_tasks,
                max_attempts_per_task=args.max_attempts,
                max_llm_calls=args.max_llm_calls,
                timeout_seconds=args.timeout_seconds,
            ),
        )
        application = application_builder(
            data_root=data_root,
            workspaces=_workspace_specs(args),
        )
        run = application.controller.create_run(
            args.run_id or _new_run_id(),
            request,
        )
    else:
        existing = store_factory(data_root).load(args.run_id)
        application = application_builder(
            data_root=data_root,
            workspaces=_specs_for_existing_run(args, existing),
        )
        if args.command == "resume":
            run = application.controller.run_until_stable(args.run_id)
        else:
            question = existing.pending_question
            if question is None:
                raise ValueError(f"Run {args.run_id} has no pending question")
            values = dict(
                _assignment(value, label="--field") for value in args.field
            )
            run = application.controller.answer_question(
                args.run_id,
                UserAnswer(
                    question_id=question.id,
                    values=values,
                    answered_at=datetime.now(UTC),
                ),
            )

    _render_run(run)
    if run.status == RunStatus.PAUSED and not run.workspaces:
        print(
            "Note: repeat --workspace or --git when answering/resuming this Run "
            "because no workspace has been persisted yet."
        )
    return _exit_code(run)


def main() -> int:
    try:
        return cli()
    except KeyboardInterrupt:
        print("Interrupted; persisted Run state is unchanged.", file=sys.stderr)
        return 130
    except Exception as error:
        print(f"resagent2: {error}", file=sys.stderr)
        return EXIT_FAILED
