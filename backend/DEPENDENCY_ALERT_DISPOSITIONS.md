# Backend dependency alert dispositions

This file records backend dependency alerts that cannot be fixed only by
updating `backend/requirements.txt` and regenerating `backend/requirements.lock.txt`.

## GHSA-wj6h-64fc-37mp (`ecdsa`)

- Issue: The `ecdsa` advisory has no patched upstream release, so a lockfile refresh alone cannot remove the Dependabot alert.
- Current dependency path: `agr-curation-api-client==0.9.0` -> `fastapi-okta==1.4.0` and `agr-cognito-py==0.1.0` -> `python-jose==3.5.0` -> `ecdsa==0.19.2`.
- Why it is still present: this repository imports `agr_curation_api.db_methods.DatabaseMethods` in `backend/src/lib/database/curation_resolver.py`, and the latest published `agr-curation-api-client` release on PyPI is still `0.9.0` with those auth dependencies bundled unconditionally.
- In-repo disposition: keep `agr-curation-api-client` on the latest published release, remove any now-unused direct auth packages from this repo, and rely on the in-repo PyJWT-based auth stack for runtime JWT validation. The transitive `ecdsa` package is not imported anywhere in this repository.
- Required follow-up: manually dismiss the GitHub Dependabot alert on `backend/requirements.lock.txt` for `GHSA-wj6h-64fc-37mp` with a note that no patched `ecdsa` release exists and the only remaining path is the upstream `agr-curation-api-client` packaging chain. Revisit the dismissal when that package releases a DB-only/auth-clean dependency graph.
