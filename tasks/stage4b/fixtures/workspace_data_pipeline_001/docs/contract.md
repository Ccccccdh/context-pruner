# Record preparation contract

- Identifiers are trimmed and converted to lowercase.
- Duplicate identifiers are removed while preserving first-seen order.
- Only records whose `active` field is exactly `True` are retained.
