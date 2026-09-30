# Authentication runtime

Install with `pip install backend/auth_runtime` from the repository root. Both
backend and TraceReview Docker images install this package independently of their
`src` packages. Consumers import `auth_runtime.base`, `auth_runtime.factory`,
`auth_runtime.oidc`, and `auth_runtime.browser` directly.

`create_auth_provider(dev_mode=...)` reads the canonical `AUTH_PROVIDER` and
`OIDC_*`/`COGNITO_*` environment settings. The service supplies its authorized
dev-mode decision; missing provider configuration never grants a bypass. Cognito
uses the generic OIDC implementation. The runtime contains no Alliance issuer,
domain, or deployment defaults.

Browser helpers share PKCE generation, validated callback exchange, and HttpOnly
session cookies. Services keep their own routes, callback destinations, user
provisioning, and authorization policy. Limits use the documented `AUTH_*`
environment settings in the repository `.env.example`.
