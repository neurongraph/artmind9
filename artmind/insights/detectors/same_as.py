from artmind.insights.detectors import Detector, Skip

SAME_AS_CANDIDATES = Detector("same_as_candidates", "health", lambda ctx: Skip("not implemented"), lambda ctx, t: [])
