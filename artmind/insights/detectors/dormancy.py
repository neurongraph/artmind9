from artmind.insights.detectors import Detector, Skip

DORMANCY = Detector("dormancy", "corpus", lambda ctx: Skip("not implemented"), lambda ctx, t: [])
