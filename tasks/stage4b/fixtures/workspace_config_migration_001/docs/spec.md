# v2 configuration contract

- Service URLs must use HTTPS and retain the configured host and port.
- Millisecond timeouts are exposed to callers as whole seconds.
- Text flags are enabled only for `1`, `true`, `yes`, or `on`, ignoring case and surrounding whitespace.
