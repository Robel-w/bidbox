# Adding an indicator

Indicators are small, typed functions registered by metadata and discovered automatically from `bidbox/indicators/`.

1. Copy `docs/indicator_template.py` into `bidbox/indicators/`.
2. Give the function a typed input and return type, and add a neutral `IndicatorMetadata` value.
3. Keep the function observational: it must not rank suppliers, select an award, or make an unsupported claim.
4. Add a unit test for normal, edge, and empty-data behavior.
5. Call it through `get_indicator("name").function(...)` from tools or scans.

The registry imports indicator modules once at first lookup. Duplicate names fail immediately, which keeps tool boundaries explicit for future contributors.
