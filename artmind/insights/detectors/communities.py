from artmind.insights.detectors import Detector, Skip

COMMUNITIES = Detector("communities", "corpus", lambda ctx: Skip("not implemented"), lambda ctx, t: [])
