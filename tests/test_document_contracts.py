import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
REMEDIATION_REQUIREMENTS = {
    "req-tg-private-relay-v1",
    "req-account-management-bot-flow-v1",
}
VALIDATION_MESSAGE_IDS = {
    "relay-hardening-dispatch",
    "relay-hardening-failure-split",
    "relay-hardening-mapping",
    "relay-hardening-slots",
    "relay-hardening-account-state",
    "relay-hardening-media",
    "relay-hardening-listener",
    "relay-hardening-worker",
    "relay-hardening-role-health",
    "relay-hardening-scope",
}
FORBIDDEN_CONTRACTS = {
    "time TTL introduced": r"(?<!no )(?<!not )\btime(?:-based)? ttl is (?!not\b)(?:introduced|enabled|implemented)\b",
    "default TTL": r"(?<!no )(?<!not )\b(?:a )?default ttl(?: is (?:introduced|enabled|applied|created|set)|\s*[:=]| of \d+)",
    "silent media size limit": r"(?<!no )(?<!not )\bsilent media size limit(?: is)? (?:introduced|enabled|applied|set)\b",
    "exactly-once guaranteed": r"(?<!no )(?<!not )\bexactly-once(?: is)? guaranteed\b",
    "E4 passed": r"(?<!no )(?<!not )\be4(?:(?:[ _]is)?[ _])passed\b",
    "production fixed": r"(?<!no )(?<!not )\bproduction(?:(?:[ _]is)?[ _])fixed\b",
}
NEGATION_BEFORE_MATCH = re.compile(
    r"(?:\b(?:no|not|never|without|forbidden|prohibited)\b|不宣称|不允许|不得|禁止|未|无|不)"
    r"[^.!?。；;]{0,48}$"
)


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8").lower()


def _assert_contracts(document: str, contracts: tuple[str, ...]) -> None:
    missing = [contract for contract in contracts if contract.lower() not in document]
    assert not missing, f"missing document contracts: {missing}"


def _section(document: str, heading: str) -> str:
    lines = document.splitlines()
    start = next(
        (index for index, line in enumerate(lines) if line.strip() == heading),
        None,
    )
    assert start is not None, f"missing section: {heading}"
    end = next(
        (
            index
            for index in range(start + 1, len(lines))
            if lines[index].startswith("## ")
        ),
        len(lines),
    )
    return "\n".join(lines[start + 1 : end])


def _table_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _table_rows(section: str) -> list[dict[str, str]]:
    table_lines = [line for line in section.splitlines() if line.startswith("|")]
    assert len(table_lines) >= 2, "section must contain a Markdown table"
    headers = _table_cells(table_lines[0])
    rows = []
    for line in table_lines[2:]:
        cells = _table_cells(line)
        assert len(cells) == len(headers), f"malformed table row: {line}"
        rows.append(dict(zip(headers, cells, strict=True)))
    return rows


def _assert_no_forbidden_contracts(document: str) -> None:
    violations = []
    for label, pattern in FORBIDDEN_CONTRACTS.items():
        matches = (
            match
            for line in document.splitlines()
            for match in re.finditer(pattern, line)
            if NEGATION_BEFORE_MATCH.search(line[: match.start()]) is None
        )
        if next(matches, None) is not None:
            violations.append(label)
    assert not violations, f"forbidden opposite contracts: {violations}"


def test_relay_prd_freezes_dispatch_and_error_contracts() -> None:
    document = _read("docs/product/tg-private-relay-v1.md")

    _assert_contracts(
        document,
        (
            "Outgoing dispatch: pending, sending, sent, failed, uncertain",
            "BotPushMessage dispatch: pending, sending, sent, failed, uncertain",
            "Incoming album batch dispatch: pending, sending, sent, failed, uncertain",
            "DeliveryFailure",
            "SessionFailure",
            "Only SessionFailure may fail over",
            "DeliveryFailure does not change TgSessionSlot health",
            "uncertain is not retried automatically",
            "remote success followed by a database update failure becomes uncertain",
            "exactly-once is not claimed",
        ),
    )


def test_relay_prd_freezes_mapping_media_and_scope_contracts() -> None:
    document = _read("docs/product/tg-private-relay-v1.md")

    _assert_contracts(
        document,
        (
            "ReplyMapping states: active, expired",
            "invalidated_at",
            "time-based TTL is not introduced and remains unproven",
            "Disabling or deleting an account expires its ReplyMapping records",
            "TG_V_CHAT_MEDIA_ROOT",
            "staging, ready, sent, failed, released",
            "No silent media size limit",
            "No groups or channels",
            "No additional media types",
            "Unsupported incoming media fails explicitly",
            "no fake BotPushMessage or ReplyMapping",
        ),
    )


def test_account_prd_freezes_independent_slot_authorization() -> None:
    document = _read("docs/product/account-management-bot-flow-v1.md")

    _assert_contracts(
        document,
        (
            "account.slot.bind:{account_id}:{slot}",
            "account.slot.reauth:{account_id}:{slot}",
            "primary, standby_1, and standby_2 are authorized independently",
            "The primary session must not be copied",
            "primary usable => active",
            "primary unusable and any standby usable => degraded",
            "all authorized slots unusable => reauth_required",
            "disabled and deleted accounts are not recomputed",
            "An unauthorized standby does not cause degradation",
        ),
    )


def test_product_index_marks_remediation_and_e4_boundary() -> None:
    document = _read("docs/product/product-index.md")
    requirements = _table_rows(_section(document, "## requirements"))
    requirements_by_id = {row["requirement_id"]: row for row in requirements}

    for requirement_id in REMEDIATION_REQUIREMENTS:
        assert requirement_id in requirements_by_id
        status = requirements_by_id[requirement_id]["status"]
        assert "remediation_" in status
        assert "e4 unproven" in status
        assert "e4 passed" not in status


def test_dataflow_freezes_durable_dispatch_and_media_spool() -> None:
    document = _read("docs/index/dataflow.md")

    _assert_contracts(
        document,
        (
            "pending -> sending -> sent | failed | uncertain",
            "BotPushMessage dispatch",
            "Incoming album batch dispatch",
            "TG_V_CHAT_MEDIA_ROOT",
            "staging -> ready -> sent -> released",
            "staging | ready -> failed -> released",
            "uncertain is never retried automatically",
            "Only SessionFailure advances to another slot",
            "DeliveryFailure leaves session health unchanged",
        ),
    )


def test_validation_matrix_has_rows_and_evidence_boundary() -> None:
    document = _read("docs/qa/validation-plan.md")
    matrix_rows = _table_rows(_section(document, "## validation matrix"))
    message_ids = [row["message_id"] for row in matrix_rows]

    assert len(message_ids) == len(VALIDATION_MESSAGE_IDS)
    assert set(message_ids) == VALIDATION_MESSAGE_IDS
    assert all(row["result"] == "unproven" for row in matrix_rows)
    _assert_contracts(
        document,
        (
            "Local and CI evidence can prove only E3",
            "real Telegram and production remain E4 unproven",
        ),
    )


def test_truth_sources_reject_opposite_contracts() -> None:
    truth_sources = (
        "docs/product/tg-private-relay-v1.md",
        "docs/product/account-management-bot-flow-v1.md",
        "docs/product/product-index.md",
        "docs/index/dataflow.md",
        "docs/qa/validation-plan.md",
    )
    document = "\n".join(_read(path) for path in truth_sources)

    _assert_no_forbidden_contracts(document)


@pytest.mark.parametrize(
    "opposite_contract",
    (
        "time-based TTL is introduced",
        "a default TTL is set",
        "default TTL: 30 days",
        "silent media size limit is enabled",
        "exactly-once is guaranteed",
        "E4_is_passed",
        "production is fixed",
    ),
)
def test_opposite_contract_matcher_rejects_affirmatives(
    opposite_contract: str,
) -> None:
    with pytest.raises(AssertionError, match="forbidden opposite contracts"):
        _assert_no_forbidden_contracts(opposite_contract.lower())


@pytest.mark.parametrize(
    "negative_contract",
    (
        "time-based TTL is not introduced",
        "no default TTL is introduced",
        "no silent media size limit is introduced",
        "we do not claim exactly-once guaranteed",
        "E4 is not passed",
        "不得宣称 production fixed",
    ),
)
def test_opposite_contract_matcher_allows_normative_negations(
    negative_contract: str,
) -> None:
    _assert_no_forbidden_contracts(negative_contract.lower())
