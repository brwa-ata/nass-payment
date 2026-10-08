# Updating nass-payment

How a change reaches every project that uses this package, in two halves:

1. **[Releasing a new version](#part-1-releasing-a-new-version)**: change the
   package, test it, and publish it as a new tag.
2. **[Updating a project](#part-2-updating-a-project)**: move one project's
   pin to that tag and deploy it.

Every project pins an exact tag (`v0.1.0`), so **nothing changes in a project
until someone moves its pin**. Releasing is safe; each project upgrades on its
own schedule.

---

## Contents

- [Projects using this package](#projects-using-this-package)
- [Which version number to bump](#which-version-number-to-bump)
- [Part 1: Releasing a new version](#part-1-releasing-a-new-version)
- [Part 2: Updating a project](#part-2-updating-a-project)
- [When Nass changes their API](#when-nass-changes-their-api)
- [Rolling back](#rolling-back)
- [Troubleshooting](#troubleshooting)

---

## Projects using this package

Keep this list current: after a release, it is the checklist of projects to
update.

| Project | Where the pin lives | Current tag |
| --- | --- | --- |
| Lavender (`lavender-system`) | `pyproject.toml` (`[tool.uv.sources]`) | `v0.1.2` |

---

## Which version number to bump

Versions are `MAJOR.MINOR.PATCH`. While the package is `0.x`:

| Bump | Command | When | Example |
| --- | --- | --- | --- |
| Patch `0.1.0 → 0.1.1` | `uv version --bump patch` | A fix. Projects change nothing but the pin | Nass renamed a field in its answer; the client now reads the new name |
| Minor `0.1.1 → 0.2.0` | `uv version --bump minor` | A new feature, **or** anything that makes a project edit its own code or settings | A new required setting; a renamed function |

When the package has settled, release `1.0.0`. From then on a change that
makes projects edit their code bumps the major (`1.4.2 → 2.0.0`), and a new
feature that needs no edits bumps the minor.

Two rules never bend:

- **A pushed tag is permanent.** Projects install by tag, so never move,
  delete or reuse one. Fix a bad release by releasing the next version.
- **Every release has a `CHANGELOG.md` entry**, and anything a project must
  do goes under **Upgrade notes**. That entry is the only thing a person
  updating a project reads.

---

## Part 1: Releasing a new version

### 0. One-time setup

```bash
cd ~/development/web
git clone https://github.com/brwa-ata/nass-payment   # skip if already there
cd nass-payment
uv sync                                          # dev tools: pytest, ruff
```

### 1. Make the change, with a test

Start from an up-to-date `main`:

```bash
git checkout main && git pull
```

Edit the code under `src/nass_payment/`. Add or update a test under `tests/`
that fails without your change: `test_client.py` for anything Nass-facing,
`test_service.py` for receipts, `test_views.py` for the callback and the
endpoints. The tests never call Nass; mock it the way the existing tests do.

If the change affects how the package is used, update `README.md` in the
same commit.

### 2. Try it inside a real project before releasing

From the project's folder, run the project against your local copy **without
touching its `pyproject.toml` or lock file**:

```bash
cd ~/development/web/lavender-system
uv run --with-editable ../nass-payment python manage.py test api.tests.test_nass_pay
uv run --with-editable ../nass-payment python manage.py runserver
```

`--with-editable` layers your working copy over the pinned version for that
one command; plain `uv run` goes straight back to the pinned tag.

For anything that changes what is sent to Nass, also run the UAT check
from the README ([Testing → Against Nass's UAT environment](README.md#against-nasss-uat-environment))
with `--with-editable ../nass-payment`, and confirm Nass accepts it.

### 3. Run the checks

Back in the package:

```bash
cd ~/development/web/nass-payment
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

All three must pass. They are exactly what GitHub Actions runs.

### 4. Bump the version

Pick the bump from the [table above](#which-version-number-to-bump). Preview
it, then apply it:

```bash
uv version --bump patch --dry-run   # nass-payment 0.1.0 => 0.1.1
uv version --bump patch             # writes pyproject.toml and uv.lock
uv version --short                  # 0.1.1
```

### 5. Add the changelog entry

At the top of `CHANGELOG.md`, under the intro, add:

```markdown
## v0.1.1

- Fixed: <what was wrong, in words a project owner understands>.

Upgrade notes: none.
```

For a release that needs project changes, spell each one out:

```markdown
## v0.2.0

- Added: <feature>.

Upgrade notes:

- Add `NASS_SOMETHING = '...'` to settings.
- Rename `service.old_name()` to `service.new_name()` in your views.
```

### 6. Commit, tag and push

```bash
git add -A
git commit -m "Release v0.1.1: <one-line summary>"
git tag v0.1.1
git push origin main --tags
```

The tag must match the version in `pyproject.toml` (`v` + `uv version --short`).

### 7. Wait for CI to pass on the tag

Open <https://github.com/brwa-ata/nass-payment/actions> and wait for the run on
`v0.1.1` to go green. It tests Python 3.10 with the oldest supported
dependencies, Python 3.13 with the newest, and lint.

**If it fails, do not update any project.** Fix the problem and release the
next patch (`v0.1.2`); leave the failed tag alone.

---

## Part 2: Updating a project

Do this in each project from the
[list above](#projects-using-this-package) that needs the change.

### 1. Read the changelog

Read every `CHANGELOG.md` entry between the project's current tag and the new
one, and note each **Upgrade notes** item. To see the current tag:

```bash
grep -A1 "tool.uv.sources" pyproject.toml
uv run python -c "import nass_payment; print(nass_payment.__version__)"
```

### 2. Move the pin

With uv, in the project's folder:

```bash
uv add git+https://github.com/brwa-ata/nass-payment --tag v0.1.1
```

This updates the tag in `[tool.uv.sources]`, re-locks `uv.lock` and installs
the new version. No other dependency moves unless the new release needs it.

A project that installs with **pip** instead keeps the pin in
`requirements.txt`: change the tag in its line, then run
`pip install -r requirements.txt`.

```
nass-payment @ git+https://github.com/brwa-ata/nass-payment@v0.1.1
```

### 3. Apply the upgrade notes

Make every change the changelog asks for: settings, imports, renamed
functions. Skip this step only when every entry says "Upgrade notes: none".

### 4. Test

```bash
uv run python manage.py check
uv run python manage.py test            # at least the project's Nass tests
```

For a release that changed what is sent to Nass, run the UAT check once
more with the project's own settings.

### 5. Commit and deploy

Commit `pyproject.toml` and `uv.lock` (or `requirements.txt`, for a pip
project) and any upgrade-note changes together, e.g.
`Update nass-payment to v0.1.1`.

On the server, the deploy installs from the lock file (`uv sync`, or any
`uv run` command, which syncs first). The server needs `git` installed and
access to github.com, because the package is fetched from the repository.

**Restart the app server and every worker** (Daphne/Gunicorn, Celery). A
running process keeps the old code and its cached `NASS_*` settings until
it restarts.

### 6. Confirm and record

On the server:

```bash
uv run python -c "import nass_payment; print(nass_payment.__version__)"   # 0.1.1
```

Then update the project's **Current tag** in the
[list above](#projects-using-this-package) and push that change to the
package repo.

---

## When Nass changes their API

The reason this is a package: fix the gateway once, then roll it out.

1. **Reproduce it against Nass's UAT environment.** Run the README's UAT
   check, or the failing call, and save Nass's answer (the request and the
   response, with credentials removed).
2. **Write a test from that answer** in `tests/test_client.py` that fails on
   the current code.
3. **Fix `client.py`** (or `service.py`/`views.py`) until the test passes,
   then check Nass's UAT environment accepts the new behaviour
   ([step 2](#2-try-it-inside-a-real-project-before-releasing)).
4. **Release it** ([Part 1](#part-1-releasing-a-new-version)): a patch if
   projects change nothing, a minor if they need a new setting.
5. **Update every project** in the list
   ([Part 2](#part-2-updating-a-project)), starting with the one that noticed
   the problem.

If Nass announces the change ahead of time, release the fix first and
update the projects before their switch-over date.

---

## Rolling back

A project that misbehaves after an upgrade goes back to its previous tag the
same way it moved forward:

```bash
uv add git+https://github.com/brwa-ata/nass-payment --tag v0.1.0
```

(A pip project sets the old tag back in `requirements.txt`.) Commit, deploy
and restart. Then fix the problem in the package and release a **new**
version. Never re-point the broken tag.

---

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `uv add` cannot find the tag | The tag was created but not pushed | `git push origin v0.1.1` (or `git push origin --tags`) |
| `uv add` fails to resolve dependencies | The new release needs a newer Django / DRF / requests than the project pins | Upgrade that dependency in the project too, or relax the range in the package and release again |
| The project still behaves the old way | Old version still installed, or processes not restarted | Check `nass_payment.__version__`; run `uv sync`; restart the app server and workers |
| Deploy fails fetching `nass-payment` | The server has no `git`, or cannot reach github.com | Install `git`; allow outbound HTTPS to github.com |
| CI red on the release tag | A test, lint or format check failed on GitHub | Don't update projects; fix and release the next patch |
| `uv version --bump` changed the wrong number | Wrong bump picked | Before tagging, run `uv version 0.1.1` to set it exactly |
