from artmind.insights.detectors import Detector, Skip

SPLIT_SIGNAL = Detector("split_signal", "health", lambda ctx: Skip("not implemented"), lambda ctx, t: [])
