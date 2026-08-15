# Releasing ledger-tieout

The distribution is **ledger-tieout**; the import package is `ledger_core` and
the repository is `ledger-core`. They differ because `ledgercore` already
exists on PyPI and the name comparison there strips separators, so
`ledger-core` and every punctuation variant of it collide with it. Only the
distribution name moved — nothing in the library was renamed.


Publishing runs on a tag push and is driven by
[`.github/workflows/release.yml`](../.github/workflows/release.yml). There is
no PyPI token in this repository — PyPI is configured to trust that one
workflow file, in this one repository, and GitHub proves the identity at run
time with a short-lived OIDC token. Nothing long-lived exists to leak.

## One-time setup on PyPI

Do this once, before the first release. It cannot be done from CI or by any
tooling in this repo; it requires being signed in as the PyPI account that
will own the project.

`ledger-tieout` does not exist on PyPI yet, so the publisher is registered as a
**pending publisher** — PyPI holds the configuration and creates the project on
the first successful upload.

1. Sign in to [pypi.org](https://pypi.org) and go to **Your account →
   Publishing**.
2. Under **Add a new pending publisher**, choose GitHub and fill in:

   | Field | Value |
   |---|---|
   | PyPI Project Name | `ledger-tieout` |
   | Owner | `floresgl1` |
   | Repository name | `ledger-core` |
   | Workflow name | `release.yml` |
   | Environment name | `pypi` |

3. Save it.

Every one of those five values is matched exactly at upload time. The workflow
filename and the environment name are the two that drift — renaming
`release.yml` or the `environment:` block in it breaks publishing until the
publisher is updated to match.

The `pypi` environment is created on GitHub the first time the workflow runs.
Creating it yourself beforehand — **Settings → Environments** — is only worth
doing to add a required reviewer, which puts a human approval between the tag
and the upload. No change to the workflow is needed either way.

## Cutting a release

1. Land everything on `main` and let CI go green.
2. Move the release's entries out of `## [Unreleased]` in
   [`CHANGELOG.md`](../CHANGELOG.md) and date the version heading.
3. Set `version` in `pyproject.toml`. The tag must match it — the workflow
   checks, and refuses rather than publishing a version number that disagrees
   with the tag someone typed.
4. Commit, then tag and push:

   ```sh
   git tag -a v0.1.0 -m "ledger-tieout 0.1.0"
   git push origin v0.1.0
   ```

The workflow then builds, verifies, and publishes. The build job runs the test
suite, `twine check`, and the assertions that `py.typed` and `LICENSE` are
actually inside the wheel; only if all of that passes does the separate publish
job — the only one holding `id-token: write` — upload.

## Things that are not undoable

A PyPI version number is permanent. Deleting a release does not free it: the
same version can never be uploaded again, and the fix for a bad upload is
always a new version, never a corrected one. This is why the tag/version check
and the wheel assertions run before the upload rather than after it.

Everything else is recoverable. A failed publish leaves the tag in place —
delete it, fix, and re-tag.

## Verifying a release

```sh
pip install ledger-tieout==<version>
python -c "import ledger_core; print(ledger_core.__file__)"
```

The wheel should carry `py.typed` beside the modules. Its absence is not
visible from a source checkout, which is exactly why CI asserts it.
