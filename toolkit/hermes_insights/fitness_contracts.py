"""Validated fitness measurement fields and plausibility bounds."""

KIND_FIELDS = {"strength": ("load_kg", "reps"), "hold": ("seconds",),
               "timed": ("seconds",), "control": ("rating",), "distance": ("cm",),
               "rom": ("degrees",), "binary": ("passed",)}


FT_CLAMPS = {"load_kg": (0, 1000), "reps": (1, 15), "seconds": (0, 3600),
             "rating": (1, 3), "cm": (-50, 60), "degrees": (0, 300),
             "passed": (0, 1)}
