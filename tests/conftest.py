"""Global test isolation for the eval SQLite store.

Must be imported BEFORE any test touches guandan.eval.store: the store
resolves its DB path on first connection (env GUANDAN_EVAL_DB wins over
the production default data/eval_history.db). Setting the env here sends
every test-written run into a pytest tmp file, so the production history
page stays clean.

Individual tests that explicitly call store.reset_for_tests(path) keep
working — they override the env per-fixture.
"""

import os
import tempfile

if "GUANDAN_EVAL_DB" not in os.environ:
    os.environ["GUANDAN_EVAL_DB"] = os.path.join(
        tempfile.gettempdir(), "guandan_eval_history_pytest.db")
