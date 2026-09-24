"""Tests for the shared identifier-validation helper used by every tool."""

from __future__ import annotations

import pytest

from tools.validation import require_identifier


def test_require_identifier_accepts_well_formed_value():
    assert require_identifier("M-1001", "member_id") == "M-1001"


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        None,
        " M-1001",
        "M-1001 ",
        "M 1001",
        12345,
    ],
)
def test_require_identifier_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        require_identifier(value, "member_id")
