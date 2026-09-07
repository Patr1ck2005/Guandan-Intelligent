"""Global test isolation for the eval SQLite store and scenario bank.

Must be imported BEFORE any test touches guandan.eval.store: the store
resolves its DB path on first connection (env GUANDAN_EVAL_DB wins over
the production default data/eval_history.db). Setting the env here sends
every test-written run into a pytest tmp file, so the production history
page stays clean.

Individual tests that explicitly call store.reset_for_tests(path) keep
working — they override the env per-fixture.

GUANDAN_SCENARIO_BANK likewise redirects the synthetic-scenario
persistence file so tests never write data/scenario_bank.json in the
project tree.
"""

import os
import tempfile

if "GUANDAN_EVAL_DB" not in os.environ:
    os.environ["GUANDAN_EVAL_DB"] = os.path.join(
        tempfile.gettempdir(), "guandan_eval_history_pytest.db")

if "GUANDAN_SCENARIO_BANK" not in os.environ:
    # Unique per pytest session (and deleted afterwards) so leftovers from
    # a previous run can never leak into a fresh ScenarioBank() instance.
    _fd, _bank_tmp = tempfile.mkstemp(suffix="-guandan_scenario_bank_pytest.json")
    os.close(_fd)
    os.environ["GUANDAN_SCENARIO_BANK"] = _bank_tmp

    import atexit
    atexit.register(lambda: os.path.exists(_bank_tmp)
                    and os.remove(_bank_tmp))
