import os
import tempfile

_D = tempfile.mkdtemp(prefix="applypilot-test-")
os.environ["DATA_DIR"] = _D
os.environ["DATABASE_URL"] = f"sqlite:///{_D}/test.db"
os.environ["RUN_WORKER_IN_PROCESS"] = "false"
os.environ["LOG_FORMAT"] = "text"
os.environ["LOG_LEVEL"] = "WARNING"
os.environ["ALLOW_SIGNUP"] = "true"
for k in ("JSEARCH_API_KEY", "ADZUNA_APP_ID", "ADZUNA_APP_KEY", "USAJOBS_API_KEY", "ARBEITSAGENTUR"):
    os.environ.pop(k, None)
os.environ["CAREER_SITES_BUDGET"] = "0"  # unit tests never hit the 10k live boards (test_career_sites_* mocks them)
