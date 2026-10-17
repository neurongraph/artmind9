from artmind.insights.detectors import Detector, Skip

SALIENCE_TREND = Detector("salience_trend", "corpus", lambda ctx: Skip("not implemented"), lambda ctx, t: [])
TYPE_TREND = Detector("type_trend", "corpus", lambda ctx: Skip("not implemented"), lambda ctx, t: [])
