from .greenhouse import GreenhouseSubmitter
from .lever import LeverSubmitter
from .ashby import AshbySubmitter
from .workday import WorkdaySubmitter
from .base import SubmitError

SUBMITTERS = {"greenhouse": GreenhouseSubmitter, "lever": LeverSubmitter, "ashby": AshbySubmitter, "workday": WorkdaySubmitter}
