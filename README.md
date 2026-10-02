<!--
SPDX-License-Identifier: Apache-2.0
SPDX-FileCopyrightText: 2026 The Linux Foundation
-->

# 🚀 Nexus Staging Action

<!-- prettier-ignore-start -->
<!-- markdownlint-disable-next-line MD013 -->
[![Linux Foundation](https://img.shields.io/badge/Linux-Foundation-blue)](https://linuxfoundation.org/) [![Source Code](https://img.shields.io/badge/GitHub-100000?logo=github&logoColor=white&color=blue)](https://github.com/lfreleng-actions/nexus-staging-action) [![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0) [![pre-commit.ci status badge]][pre-commit.ci results page] [![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/lfreleng-actions/nexus-staging-action/badge)](https://scorecard.dev/viewer/?uri=github.com/lfreleng-actions/nexus-staging-action)
<!-- prettier-ignore-end -->

Composite GitHub Action to manage the Sonatype Nexus 2 staging repository
lifecycle via REST API. Pure bash+curl implementation — no lftools or Python
dependency required.

## Features

- **Stage**: Create staging repo, upload Maven artifacts, close it and wait
  for Nexus to finish validating it
- **Close**: Close an existing staging repository and wait for the result
- **Release**: Release a closed staging repository to the releases repository
  (the `promote` mode name is a deprecated alias for `release`)
- **Drop**: Drop/delete a staging repository (cleanup on failure)
- Compatible with Nexus 2 staging API
- Writes `staging-repo.txt` in JJB-compatible format
- Generates GitHub Actions step summary

<!-- markdownlint-disable MD013 MD060 -->

## Nexus 2 REST API Reference

<!-- markdownlint-disable MD013 -->

| Operation | Method | Endpoint                                                       |
| --------- | ------ | -------------------------------------------------------------- |
| Create    | POST   | `/service/local/staging/profiles/{profile-id}/start`           |
| Upload    | PUT    | `/service/local/staging/deployByRepositoryId/{repo-id}/{path}` |
| Close     | POST   | `/service/local/staging/profiles/{profile-id}/finish`          |
| Status    | GET    | `/service/local/staging/repository/{repo-id}`                  |
| Verify    | GET    | `/service/local/staging/repository/{repo-id}/activity`         |
| Release   | POST   | `/service/local/staging/bulk/promote`                          |
| Drop      | POST   | `/service/local/staging/profiles/{profile-id}/drop`            |

<!-- markdownlint-enable MD013 MD060 -->

Close and drop use XML payloads. The action escapes the XML special
characters `& < > " '` in `description` before sending it:

```xml
<promoteRequest><data>
  <description>text</description>
  <stagedRepositoryId>repo-id</stagedRepositoryId>
</data></promoteRequest>
```

Release matches `lftools nexus release`: it first verifies the repo
is in a closed state (via the activity endpoint, failing on `ruleFailed` or
`repositoryCloseFailed`), then releases via `bulk/promote` with a JSON payload,
and polls the activity endpoint until `repositoryReleased`:

```json
{ "data": { "stagedRepositoryIds": ["repo-id"] } }
```

## Usage

### Stage Mode (Create + Upload + Close)

```yaml
- name: 'Stage Maven artifacts to Nexus'
  id: nexus-stage
  uses: lfreleng-actions/nexus-staging-action@main
  with:
    nexus-server: 'https://nexus.opendaylight.org'
    nexus-username: ${{ secrets.NEXUS_USERNAME }}
    nexus-password: ${{ secrets.NEXUS_PASSWORD }}
    staging-profile-id: ${{ vars.STAGING_PROFILE_ID }}
    mode: 'stage'
    m2repo-path: 'm2repo'
    description: 'CI build ${{ github.run_id }}'
```

### Release Mode

```yaml
- name: 'Release staging repository'
  uses: lfreleng-actions/nexus-staging-action@main
  with:
    nexus-server: 'https://nexus.opendaylight.org'
    nexus-username: ${{ secrets.NEXUS_USERNAME }}
    nexus-password: ${{ secrets.NEXUS_PASSWORD }}
    staging-profile-id: ${{ vars.STAGING_PROFILE_ID }}
    mode: 'release'
    staging-repo-id: ${{ needs.stage.outputs.staging-repo-id }}
```

### Close Mode

```yaml
- name: 'Close staging repository'
  uses: lfreleng-actions/nexus-staging-action@main
  with:
    nexus-server: 'https://nexus.opendaylight.org'
    nexus-username: ${{ secrets.NEXUS_USERNAME }}
    nexus-password: ${{ secrets.NEXUS_PASSWORD }}
    staging-profile-id: ${{ vars.STAGING_PROFILE_ID }}
    mode: 'close'
    staging-repo-id: 'example-1234'
```

### Drop Mode (Cleanup)

```yaml
- name: 'Drop staging repository'
  if: failure()
  uses: lfreleng-actions/nexus-staging-action@main
  with:
    nexus-server: 'https://nexus.opendaylight.org'
    nexus-username: ${{ secrets.NEXUS_USERNAME }}
    nexus-password: ${{ secrets.NEXUS_PASSWORD }}
    staging-profile-id: ${{ vars.STAGING_PROFILE_ID }}
    mode: 'drop'
    staging-repo-id: ${{ steps.nexus-stage.outputs.staging-repo-id }}
```

### Full Pipeline Example

```yaml
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: 'Build with Maven'
        run: mvn deploy -DaltDeploymentRepository=local::file:m2repo

  stage:
    needs: build
    runs-on: ubuntu-latest
    outputs:
      staging-repo-id: ${{ steps.stage.outputs.staging-repo-id }}
    steps:
      - name: 'Stage to Nexus'
        id: stage
        uses: lfreleng-actions/nexus-staging-action@main
        with:
          nexus-server: ${{ vars.NEXUS_SERVER }}
          nexus-username: ${{ secrets.NEXUS_USERNAME }}
          nexus-password: ${{ secrets.NEXUS_PASSWORD }}
          staging-profile-id: ${{ vars.STAGING_PROFILE_ID }}
          mode: 'stage'

  release:
    needs: stage
    runs-on: ubuntu-latest
    steps:
      - name: 'Release staging repo'
        uses: lfreleng-actions/nexus-staging-action@main
        with:
          nexus-server: ${{ vars.NEXUS_SERVER }}
          nexus-username: ${{ secrets.NEXUS_USERNAME }}
          nexus-password: ${{ secrets.NEXUS_PASSWORD }}
          staging-profile-id: ${{ vars.STAGING_PROFILE_ID }}
          mode: 'release'
          staging-repo-id: ${{ needs.stage.outputs.staging-repo-id }}
```

## Inputs

<!-- markdownlint-disable MD013 MD060 -->

| Input                 | Description                                               | Required | Default                  |
| --------------------- | --------------------------------------------------------- | -------- | ------------------------ |
| `nexus-server`        | Nexus server URL (e.g., `https://nexus.opendaylight.org`) | ✅       | —                        |
| `nexus-username`      | Nexus username for authentication                         | ✅       | —                        |
| `nexus-password`      | Nexus password for authentication                         | ✅       | —                        |
| `staging-profile-id`  | Nexus staging profile ID (per-project)                    | ✅       | —                        |
| `mode`                | Operation mode: `stage`, `close`, `release`, `drop`       | ✅       | `stage`                  |
| `m2repo-path`         | Path to local Maven repo directory (for `stage` mode)     | ❌       | `m2repo`                 |
| `staging-repo-id`     | Existing staging repo ID (for `close`/`release`/`drop`)   | ❌       | —                        |
| `description`         | Description for the staging repository                    | ❌       | `GitHub Actions staging` |
| `close-timeout`       | Seconds to wait for a close to finish (`stage`/`close`)   | ❌       | `600`                    |
| `close-poll-interval` | Seconds between close status checks (`stage`/`close`)     | ❌       | `10`                     |

<!-- markdownlint-enable MD013 MD060 -->

`close-timeout` and `close-poll-interval` take whole seconds from 1 to
86400 (one day); the action rejects any other value before contacting
Nexus.

## Outputs

<!-- markdownlint-disable MD013 MD060 -->

| Output             | Description                                  |
| ------------------ | -------------------------------------------- |
| `staging-repo-id`  | Staging repository ID (e.g., `example-1234`) |
| `staging-repo-url` | Staging repository URL                       |

<!-- markdownlint-enable MD013 MD060 -->

## How It Works

### Stage Mode

1. **Create** — POST to `/staging/profiles/{id}/start` to open a new
   staging repository
2. **Upload** — PUT each file from `m2repo-path` to
   `/staging/deployByRepositoryId/{repo-id}/{relative-path}`. Any failed
   upload fails the step before the close, as does an `m2repo-path` with
   no files, so Nexus never closes an incomplete repository
3. **Close** — POST to `/staging/profiles/{id}/finish` to close the
   repository and trigger Nexus validation rules, then wait for Nexus to
   finish (see [Waiting for a close](#waiting-for-a-close))
4. Writes `archives/staging-repo.txt` in JJB-compatible format:
   `{repo-id} {repo-url}`

If the step fails after creating the staging repository (a failed upload,
a staging rule failure, a timeout), the action logs the repository ID and
drops the repository, so failed runs leave no open repositories behind.
The drop makes a best effort: when Nexus refuses it, or does not answer
within 30 seconds (or `close-timeout`, when that is shorter), the action
logs a warning naming the repository to drop by hand, and the step still
fails with the original error.

### Waiting for a close

Nexus 2 closes a repository asynchronously and runs its staging rules
(signatures, POM and checksum checks) while doing so. After the `finish`
request succeeds, stage and close modes poll
`GET /staging/repository/{repo-id}` every `close-poll-interval` seconds:

- `<type>closed</type>` with `<transitioning>false</transitioning>`: the
  close succeeded
- a `ruleFailed` or `repositoryCloseFailed` event in the activity of this
  close attempt: the step fails, reporting each failing rule message the
  activity records. Failures of earlier close attempts on the same
  repository do not count, so closing again after a failed close can
  succeed. Release mode still refuses such a repository (see
  [Release Flow](#release-flow))
- still not closed after `close-timeout` seconds: the step fails. The time
  left bounds each status request and pause, so an unresponsive Nexus
  cannot hold the step past the timeout

`close-timeout` also bounds each request made before the polling
starts: close mode's lookup of the repository and the `finish` request.

The 600-second default gives Nexus room to run the staging rules over
a large repository while stopping a stuck close from holding a runner for
the 30 minutes release mode allows for a release.

### Release Flow

Ports `lftools nexus release`:

1. **Verify** — GET `/staging/repository/{repo-id}/activity` to confirm the
   repository is in a closed state; fail on `ruleFailed` or
   `repositoryCloseFailed`; skip if already `repositoryReleased`. Like
   lftools, this checks every close attempt in the activity log, so a
   repository that failed a close once stays unreleasable even after a
   later close succeeds: stage a fresh repository instead
2. **Release** — POST to `/staging/bulk/promote` with the JSON payload
   `{"data":{"stagedRepositoryIds":["repo-id"]}}` (expects HTTP 201)
3. **Poll** — GET the activity endpoint until `repositoryReleased`

### Close Operation

- POST to `/staging/profiles/{id}/finish` to close an
  opened staging repository, then wait for the result as described in
  [Waiting for a close](#waiting-for-a-close). Close mode first reads the
  repository activity to count earlier close attempts, and fails without
  requesting the close when it cannot. Close mode never drops a
  repository whose close fails

### Drop Operation

- POST to `/staging/profiles/{id}/drop` to delete the staging
  repository (useful for cleanup on failure)

Close and drop fail the step unless Nexus answers the POST with an HTTP
2xx status, and log the response body when it does not.

## Comparison with lftools

<!-- markdownlint-disable MD013 MD060 -->

| Feature        | lftools                        | nexus-staging-action  |
| -------------- | ------------------------------ | --------------------- |
| Runtime        | Python + pip                   | bash + curl           |
| Install        | `pip install lftools`          | None (built-in)       |
| Stage          | `lftools deploy nexus-stage`   | `mode: stage`         |
| Release        | `lftools nexus release`        | `mode: release`       |
| Drop           | Manual API call                | `mode: drop`          |
| CI Integration | Script wrapper                 | Native GitHub Action  |
| Output format  | stdout parsing                 | GitHub Action outputs |

<!-- markdownlint-enable MD013 MD060 -->

## Requirements

- Sonatype Nexus 2 server with staging profiles configured
- Nexus user account with staging permissions
- Staging profile ID for the target project
- `curl` available on the runner (default on all GitHub runners)

## License

[Apache-2.0](LICENSES/Apache-2.0.txt)

[pre-commit.ci results page]: https://results.pre-commit.ci/latest/github/lfreleng-actions/nexus-staging-action/main
[pre-commit.ci status badge]: https://results.pre-commit.ci/badge/github/lfreleng-actions/nexus-staging-action/main.svg
