from artmind.insights.detectors import Detector, Skip

KIND_MISMATCH = Detector("kind_mismatch", "health", lambda ctx: Skip("not implemented"), lambda ctx, t: [])
VOCABULARY_SPRAWL = Detector("vocabulary_sprawl", "health", lambda ctx: Skip("not implemented"), lambda ctx, t: [])
RELATIONSHIP_USAGE = Detector("relationship_usage", "health", lambda ctx: Skip("not implemented"), lambda ctx, t: [])
OPEN_CONFLICTS = Detector("open_conflicts", "health", lambda ctx: Skip("not implemented"), lambda ctx, t: [])
