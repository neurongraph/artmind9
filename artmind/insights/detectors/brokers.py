from artmind.insights.detectors import Detector, Skip

BROKERS = Detector("brokers", "corpus", lambda ctx: Skip("not implemented"), lambda ctx, t: [])
