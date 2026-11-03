from .greenhouse import GreenhouseSubmitter
from .lever import LeverSubmitter
from .ashby import AshbySubmitter
from .workday import WorkdaySubmitter
from .universal import UniversalSubmitter
from .base import SubmitError, NeedsManual, shutdown_browser

SUBMITTERS = {"greenhouse": GreenhouseSubmitter, "lever": LeverSubmitter, "ashby": AshbySubmitter, "workday": WorkdaySubmitter}


def submitter_for(ats):
    """Dedicated submitter when we have one, otherwise the universal form filler. Never 'unsupported'."""
    return SUBMITTERS.get(ats, UniversalSubmitter)
