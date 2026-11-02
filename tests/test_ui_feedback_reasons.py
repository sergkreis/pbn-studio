# SPDX-License-Identifier: GPL-3.0-only
from pbnstudio.ui import issue_explanation
from pbnstudio.document import Issue

def test_no_fit_explains_actual_minimum_and_attempted_placement():
    issue=Issue("label-no-fit",4,"",reason="inline-and-leader-no-fit-after-minimum",metadata={"min_font_pt":6.0,"preferred_font_pt":7.0,"leaders_attempted":True})
    text=issue_explanation(issue)
    assert "6" in text and "7" in text and "вынос" in text.lower()
    assert "миним" in text.lower()

def test_geometry_issue_is_not_misreported_as_number_fit():
    text=issue_explanation(Issue("geometry-unmapped",2,""))
    assert "границ" in text.lower() and "миним" not in text.lower()
