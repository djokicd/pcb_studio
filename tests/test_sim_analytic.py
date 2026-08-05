"""Full FDTD simulation tests against analytic/closed-form expectations.

The case definitions (models, reference values, tolerances) live in
cases.py and are shared with the GUI's built-in test runner:

    python3 -m pytest -m sim -v
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cases import CASES
from helpers import run_sim

pytestmark = pytest.mark.sim


@pytest.mark.parametrize('case_id', list(CASES))
def test_benchmark(case_id):
    case = CASES[case_id]
    rows = run_sim(case['build'](), case_id, timeout=600)
    metrics = case['evaluate'](rows)
    failed = [m for m in metrics if not m['pass']]
    report = '\n'.join(
        f"{'PASS' if m['pass'] else 'FAIL'}  {m['label']}: {m['value']} {m['unit']}"
        f"  (accept {m['lo']}..{m['hi']}, ref {m['ref']})"
        for m in metrics)
    assert not failed, f'{case["title"]}:\n{report}'
