# OinkLand automatic game mirror

The scheduled workflow checks the public OinkLand archive catalogue every five minutes. It uploads only archives with a new SHA-256. Each source download is verified before publication; immutable release asset names contain the full content hash.

The job uses GitHub's temporary repository-scoped `GITHUB_TOKEN` with `contents: write`. No personal GitHub token or private VDS credential is required. The VDS reads the public release and activates only matching archive digests and sizes. The origin remains available while publication is pending or fails.

The workflow is scheduled, can be dispatched manually, and also runs when its script or tests change. GitHub may delay scheduled runs. A weekly update to `mirror-health.json` keeps scheduled repository activity current.

Run publisher validation locally with:

```text
python3 -m unittest discover -s tests
```

This repository contains public game archives and the small mirror publication workflow. Launcher/backend application source and private server configuration are maintained separately.
