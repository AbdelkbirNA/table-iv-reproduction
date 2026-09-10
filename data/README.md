# Data / external artifacts

Do not commit large raw datasets or generated code dumps blindly.

Expected normalized record for each fault + criterion:

```json
{
  "fault_id": "...",
  "test_id": "...",
  "adequacy_items": ["..."],
  "triggers_fault": true,
  "detects_fault": false
}
```

`adequacy_items` is criterion-specific and should be generated separately for statement, branch, and mutation experiments.
