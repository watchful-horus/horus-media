# Contributing to Horus

Thanks for wanting to help. Horus is a small self-hosted dashboard for Plex and Emby, and it is
looked after by one person in their spare time, so a few ground rules keep it manageable.

## Before you start
- For anything bigger than a small fix, open an issue first and describe what you want to change.
  It saves you from writing something that doesn't fit.
- Questions and ideas are welcome as issues too. There is no promise of support or of a reply
  time.

## Ground rules
These protect the people who run Horus, so they are not negotiable.

1. **Read-only against Plex and Emby**, with one exception: an admin can stop a playing stream
   (`stop_session`, from a signed-in session only, never from an API key). No other changes, no
   deletes, no other playback control.
2. **Server tokens are secrets.** They are encrypted at rest, and they must never be logged, shown
   in the UI or an API response, or printed in an error message. If your change touches tokens,
   say so in the pull request.
3. **Write your own code.** Build from the official Plex and Emby API documentation. Do not copy
   code or assets from other projects (Tautulli and others use licences that are not compatible
   with this one). If you use a library, check its licence first.
4. **Trademarks.** "Plex" and "Emby" belong to their owners. Use them only to describe what
   Horus works with, never in a product name or logo.
5. **Keep personal details out.** No real IP addresses, usernames, tokens, server names or file
   paths in code, tests, screenshots or commit messages. Tests use `192.168.1.x` and made-up names.
6. **Tests use recorded or fake responses.** Never require a live Plex or Emby server.

## Setting up
[docs/install.md](docs/install.md) (From source) has the full steps. In short:

```bash
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cd web && npm ci && npm run build && cd ..
```

## Checks to run before you open a pull request
```bash
ruff check .
pytest -q
cd web && npm run build
```
The same checks run on GitHub for every pull request, and the maintainer will not merge a change while they fail.

## Pull requests
- Keep each one small and about one thing.
- Say what changed and why. Add or update tests for behaviour you change.
- Match the surrounding code's style and the way it is commented.
- The maintainer may ask for changes, or may decide not to merge something that doesn't fit.
  That is not personal.

## Licence
By contributing you agree that your contribution is released under the project's
[MIT licence](LICENSE).

## Behaviour
Be kind and assume good faith. Harassment and personal attacks are not tolerated, and the
maintainer may remove comments or block accounts that do it.
