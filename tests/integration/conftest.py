from __future__ import annotations

import pytest


@pytest.fixture
def problem_id(request):
    pid = request.config.getoption("--problem-id")
    if not pid:
        pytest.skip("pasa --problem-id P-<número>")
    return pid
