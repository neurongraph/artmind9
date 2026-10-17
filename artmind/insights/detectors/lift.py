from artmind.insights.detectors import Detector, Skip

OUTCOME_LIFT = Detector("outcome_lift", "corpus", lambda ctx: Skip("not implemented"), lambda ctx, t: [])
