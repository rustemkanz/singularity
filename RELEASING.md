# Releasing Singularity

## First public tag

The recommended first public release is `v0.1.0`.

Use that first tag to signal a useful, reviewable public prototype with explicit scope. Do not frame the first tag as a finished autonomous-platform release.

## Initial public release checklist

- Confirm all `AZURE_DEVOPS_*` examples use placeholders rather than private tenant values.
- Confirm the documented Python support in `README.md` and `CONTRIBUTING.md` still matches the CI matrix.
- Run `python3 -m unittest discover -s tests -p 'test_*.py'` and keep it green before tagging.
- Review `README.md`, `LICENSE`, `NOTICE`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, and `CONTRIBUTING.md` together as the minimum public policy surface.
- Confirm `.sg-artifacts/`, local screenshots, cached payloads, and environment files are ignored and not staged.
- Search documentation and examples for internal org names, project names, team identifiers, and private URLs.
- Verify `./sg --help` describes the intended Singularity scope.
- Update `CHANGELOG.md` with the release highlights and any notable compatibility notes.
- Choose the initial version and release date. Start with `v0.1.0` unless the scope changes materially.

## Tag and publish

```bash
git status
python3 -m unittest discover -s tests -p 'test_*.py'
git tag -a v0.1.0 -m "Singularity v0.1.0"
git push origin v0.1.0
```

After pushing the tag, draft release notes that cover:

- what the tool is
- what it is not
- setup and authentication prerequisites
- the human approval model
- known limitations in the initial public release