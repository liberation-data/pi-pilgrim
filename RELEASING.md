# Releasing

pi-pilgrim is released by CI, the same way as the Drivine libraries (drivine-python, drivine4j and drivine).

## The process

1. **Prepare release.** In the Actions tab, run "Prepare release" (`.github/workflows/prepare-release.yml`) with a
   `bump` of `patch`, `minor`, `major` or `prerelease`, or an exact `version`. It bumps `version` in `pyproject.toml`
   on a branch `release/vX.Y.Z`, and opens a PR "Release vX.Y.Z" that lists the commits since the last tag.
2. **Review and merge the release PR.** It passes CI like any other PR. Merging it is the release point.
3. **CI publishes.** The merge, a push to `main` that changes `pyproject.toml`, runs `.github/workflows/release.yml`.
   It runs the full CI as its gate, builds, checks, publishes to PyPI, and creates the GitHub release with its tag
   `vX.Y.Z` on the merge commit. Nothing is built from a laptop.
4. **Credentials.** None are held: PyPI trusts the workflow through trusted publishing (OIDC), in a GitHub
   environment named `release`. Give that environment required reviewers: now that merging publishes, their approval
   of the run is the human checkpoint before anything goes out.

A PR that "Prepare release" opens with the default `GITHUB_TOKEN` starts no workflows, so CI doesn't run on it by
itself. Push an empty commit to its branch, or close and reopen it, to start CI. With a `RELEASE_PR_TOKEN` secret set
(below), the PR is opened with that token and CI runs. "Prepare release" fails, saying why, when the version is
already tagged, isn't later than the last release, or its `release/vX.Y.Z` branch already exists.

**Tagging by hand is the fallback.** A pushed tag `vX.Y.Z` still releases the tagged commit. Use it when a
merge didn't release:

```
git switch main && git pull
git tag vX.Y.Z
git push origin vX.Y.Z
```

## What release.yml does

- **`check`** works out the version: the tag without its `v` on a tag push, or `pyproject.toml`'s version otherwise.
  - On a push to `main` that changes `pyproject.toml`, it releases when the version is new: no tag `vX.Y.Z` exists
    yet, and the version is later than every `vX.Y.Z` tag (a prerelease `vX.Y.Z-rc.N` sorts below `vX.Y.Z`).
    Otherwise the run releases nothing, and its notice says why: a change to the file that isn't a version bump, for
    example.
  - On a tag push it fails, saying why, unless the tag is `vX.Y.Z` (or `vX.Y.Z-suffix`), its version equals
    `pyproject.toml`'s, and the tagged commit is on `main`.
- **`gate`** runs the full CI, `ci.yml`, unchanged. A push to `main` that releases nothing skips it, as `ci.yml` runs
  on that push anyway.
- **`build`** runs `uv build --no-sources`, which writes the sdist and the wheel built from it to `dist/`
  (poetry-core). It fails a version PyPI already has, since PyPI refuses to take it again. Then it runs
  `twine check --strict`, and `scripts/smoke_wheel.py` on Python 3.11 and 3.14: the wheel, installed into a clean
  virtual environment, imports, and an assembly makes its components and closes its shared ones. It uploads `dist/`
  as the run's artifact.
- **`publish`** runs on a release alone, in the `release` environment. `pypa/gh-action-pypi-publish` uploads the
  exact files `build` made to [PyPI](https://pypi.org/project/pi-pilgrim/).
- **`github-release`** then creates the GitHub release, with generated notes, marked a prerelease when the version has
  a `-suffix`. On a push to `main` it also creates the tag `vX.Y.Z`, on the commit that was released. A tag made this
  way starts no workflow, so it doesn't release again.

A tag that fails `check` publishes nothing. Delete it (`git push origin :refs/tags/vX.Y.Z`), fix what the message
says, and tag again. A tag pushed by hand for a version that merging has already released fails in `build`, as PyPI
already has that version; nothing is published twice. If a release failed after publishing, re-run its failed jobs.

A **dry run** is everything but `publish` and `github-release`. Run it from the Actions tab (Release, Run workflow,
on any branch or tag), and it runs by itself on a pull request that changes the release machinery, a release PR
included. It leaves the `dist` artifact on the run: `pi_pilgrim-X.Y.Z.tar.gz` and `pi_pilgrim-X.Y.Z-py3-none-any.whl`.
A dry run of a version PyPI already has only warns.

## Versions

- **Version:** `version` in `pyproject.toml`. The tag must be `v` and that version, character for character: `v0.4.0`
  for `0.4.0`. `poetry.lock` doesn't record the project's version, so the release PR changes `pyproject.toml` alone.
- **Version arithmetic:** `scripts/release_version.py`, which `check` uses too, to compare a version with the newest
  tag. A `prerelease` bump gives `X.Y.Z-rc.N`: `0.3.0` becomes `0.3.1-rc.0`, and `0.3.1-rc.0` becomes `0.3.1-rc.1`.
  For a release candidate of a minor or major version, give the exact `version`, for example `0.4.0-rc.1`.
- **Prereleases:** write the version in `pyproject.toml` as the tag has it, with a `-`: `0.4.0-rc.1`, tagged
  `v0.4.0-rc.1`. PEP 440 normalizes it, so the distributions and PyPI call it `0.4.0rc1`, and pip installs it only
  when asked (`pip install pi-pilgrim==0.4.0rc1`, or `--pre`). `0.4.0rc1` in `pyproject.toml` can't be tagged: `check`
  wants a `-` in the tag. The suffix must be one PEP 440 reads (`a`, `b`, `rc`, `alpha`, `beta`, `c`, `pre`, `dev`,
  with a number), or the build fails before anything is published.

The same checks, locally, publishing nothing:

```
uv build --no-sources
uvx twine check --strict dist/*
python scripts/smoke_wheel.py dist/*.whl
```

## Settings, made once

- **GitHub:** Settings, Environments, a `release` environment, with required reviewers. Its deployment branches and
  tags, if limited, must allow both the branch `main` (a merged bump) and the tags `v*` (a tag pushed by hand).
- **GitHub Actions and PRs:** Settings, Actions, General, "Allow GitHub Actions to create and approve pull requests",
  so "Prepare release" can open its PR with `GITHUB_TOKEN`.
- **Optional, a `RELEASE_PR_TOKEN` secret:** a fine-grained personal access token with Contents and Pull requests
  read and write on this repository. "Prepare release" pushes its branch and opens its PR with it, so CI runs on the
  PR.
- **PyPI:** the `pi-pilgrim` project's Publishing settings, a GitHub trusted publisher with owner `liberation-data`,
  repository `pi-pilgrim`, workflow `release.yml`, and environment `release`.
