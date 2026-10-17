from artmind.insights.detectors import Detector, Skip

OPEN_LOOPS = Detector("open_loops", "corpus", lambda ctx: Skip("not implemented"), lambda ctx, t: [])
