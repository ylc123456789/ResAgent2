import pytest
from pydantic import ValidationError

from resagent2_contracts import CodeModifyResult, CodeUnderstandResult, VerificationResult


def verification(*, exit_code: int = 0, timed_out: bool = False) -> VerificationResult:
    return VerificationResult(
        command="python -m pytest",
        exit_code=exit_code,
        timed_out=timed_out,
        stdout_path=".resagent2/run/command.stdout",
        stderr_path=".resagent2/run/command.stderr",
        duration_seconds=0.1,
    )


def test_coding_payloads_round_trip() -> None:
    understand = CodeUnderstandResult(
        answer="The loop is in loop.py.",
        evidence_files=["src/loop.py"],
    )
    modify = CodeModifyResult(
        changed_files=["src/loop.py"],
        patch_path=".resagent2/run/changes.patch",
        verification_results=[verification()],
        verification_passed=True,
    )

    assert CodeUnderstandResult.model_validate_json(understand.model_dump_json()) == understand
    assert CodeModifyResult.model_validate_json(modify.model_dump_json()) == modify


def test_code_modify_result_rejects_false_verification_claim() -> None:
    with pytest.raises(ValidationError, match="verification_passed"):
        CodeModifyResult(
            changed_files=["src/loop.py"],
            patch_path=".resagent2/run/changes.patch",
            verification_results=[verification(exit_code=1)],
            verification_passed=True,
        )


def test_code_result_paths_remain_relative() -> None:
    with pytest.raises(ValidationError, match="relative"):
        CodeUnderstandResult(answer="x", evidence_files=["../secret.txt"])
