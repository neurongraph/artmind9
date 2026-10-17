from artmind.insights.detectors import Detector, Skip

TRANSITIONS = Detector("transitions", "corpus", lambda ctx: Skip("not implemented"), lambda ctx, t: [])
