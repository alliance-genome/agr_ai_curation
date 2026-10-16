# Frontend dependency cache probe

Validated on 2026-10-16 against main `e2e8279743aa5236a7933d2e262528125b619613`
plus this Dockerfile change. All ten current Vite arguments retain their defaults.

Docker documents that `RUN` implicitly uses in-scope build arguments, even without
shell expansion: [Impact on build caching](https://docs.docker.com/reference/dockerfile/#impact-on-build-caching).
The arguments therefore belong after `npm ci`. The optional npm download-cache
mount was omitted: the publishing workflow exports instruction layers through
`type=gha`, but persistence of cache mounts across fresh runners is not established.
No cache infrastructure or build-path requirements were added.

## Experiment

Docker Engine 29.1.3, Buildx 0.29.1, BuildKit 0.33.1, `linux/amd64`, fresh
`docker-container` builder `all-1403-cache-probe`. Buildx was downloaded into a
temporary directory because the VM did not have the plugin installed. The fresh
builder took time to pull its image; an additional default-builder `--no-cache`
build also succeeded. Only the disposable builder was used for comparisons.

The context came from `git archive HEAD frontend` with the edited Dockerfile
copied over it. It contained no host dependencies, generated assets, or local env
files. Neither manifests nor lockfile changed in the committed implementation.

| Probe | Changed input | `npm ci` result | Artifact result |
| --- | --- | --- | --- |
| A | Fresh builder; SHA `cache-probe-a`, mode false | Executed | Metadata false/A; compiled SHA A |
| B | Only SHA becomes `cache-probe-b` | Cached | Metadata false/B; compiled SHA B; asset build executed |
| Mode | Only mode becomes true, SHA B fixed | Cached | Metadata true/B; compiled mode true; asset build executed |
| Invalid | Mode `invalid` | Cached | Existing `VITE_DEV_MODE must be true or false` rejection |
| Manifest | Original mode false; disposable package description | Executed | Build succeeded |
| Lock | Original manifest; trailing newline added to lockfile | Executed | Build succeeded |
| Restored | Both original dependency inputs restored | Cached | Metadata false/B; compiled SHA B |

The mode comparison also checked the compiled environment object in JavaScript:
`VITE_DEV_MODE` was `false` in B and `true` in Mode. Metadata was parsed and compared
with exactly `{schema_version: 1, vite_dev_mode: <boolean>, git_sha: <SHA>}`.
Compiled SHA checks inspected `.js` files, excluding source maps.

Full plain BuildKit logs and extracted artifacts were retained for issue review
in `/tmp/all-1403-buildx.nxmVYp/`. The companion text file preserves the install
and build-step evidence. No elapsed-time speedup or production latency claim is
made. The disposable builder and local probe tags were removed after validation;
shared caches were not pruned.

## Reproduction

From a clean checkout containing the change, with Docker Buildx and Node available:

```bash
probe_dir=$(mktemp -d)
mkdir "$probe_dir/context"
git archive HEAD frontend | tar -x -C "$probe_dir/context" --strip-components=1
builder="frontend-cache-probe-$(date +%s)"
docker buildx create --name "$builder" --driver docker-container
build_probe() {
  docker buildx build --builder "$builder" --platform linux/amd64 \
    --progress plain --load -t "frontend-cache-probe:$1" \
    --build-arg "VITE_GIT_SHA=$2" --build-arg "VITE_DEV_MODE=$3" \
    "$probe_dir/context" > "$probe_dir/$1.log" 2>&1
}
build_probe a cache-probe-a false
build_probe b cache-probe-b false
build_probe mode cache-probe-b true
# Must fail with the existing strict mode validation error:
build_probe invalid cache-probe-b invalid
```

Do not prune or use `--no-cache` between these builds. Locate the actual
`[builder ...] RUN npm ci` step in each log and check that its numbered result is
`CACHED` for B and Mode; their `npm run build` steps must execute. A must execute
installation because the builder is new.

Extract each successful image using `docker create`, `docker cp
<container>:/usr/share/nginx/html/. <output-directory>`, and `docker rm`.
Parse `build-metadata.json`, assert the exact object described above, and search
only compiled `.js` assets for the expected SHA and compiled mode value.

For the manifest control, save both original package files, add an innocuous
`description` to the context's `package.json` using Node JSON parsing/writing,
and build with SHA B/mode false. Restore the manifest, append a newline to the
context's `package-lock.json`, and build again. Both installations must execute
successfully. Restore both originals and rebuild: installation must be cached.
These controls change dependency-input bytes without changing package versions
or making the lockfile inconsistent. Remove only the created builder and probe
tags when finished.

## Regression coverage

`bash scripts/testing/production_compose_contract_test.sh` passed, including the
new instruction-order assertion. The assertion rejects the original Dockerfile
and accepts the relocated argument block. `git diff --check` also passed.
No frontend source changed, so Vitest and TypeScript suites were not repeated.
GitHub Actions and the required PR review remain the downstream gates.
