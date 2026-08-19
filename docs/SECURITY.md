# Security and Privacy Boundaries

## The planner is untrusted input

Treat LLM output like input from an untrusted network client. It must be parsed, type checked, range checked, capability checked, and safety checked before execution.

The repository therefore does not expose:

- shell execution
- arbitrary subprocess arguments from the model
- arbitrary file read/write paths
- arbitrary recipient addresses
- arbitrary LAN URLs
- direct servo/channel writes
- runtime safety-code editing

## Research SSRF boundary

`SafeFetcher` accepts only HTTP/HTTPS destinations and rejects localhost, private, loopback, link-local, multicast, reserved, and unspecified resolved IPs. Redirect destinations are checked again.

Keep SearXNG bound to localhost.

## Delivery boundary

Photo delivery resolves aliases using `config/recipients.local.json`, which is ignored by Git. The model never needs literal phone numbers, email addresses, or passwords.

## Secrets

Never commit:

- SMTP passwords
- Signal account secrets
- signing private keys
- live recipient config
- live brain databases
- face embeddings

Production secrets should be owned by the Kendra service user and mode `0600` where applicable.

## Signed updates

Git hosting is transport, not trust. Production update manifests are verified with minisign and artifact SHA-256 hashes before activation.

Replace `config/minisign.pub` with your real public release key before enabling update automation.

Voice-triggered updates use a fixed GitHub repository/path allowlist and an exact confirmation phrase. The updater rejects unsigned manifests, hashes not covered by the signed manifest, unsafe archive members, dirty development trees, and artifacts over the configured limit. It builds the inactive A/B slot before any optional activation. The private release key must remain outside the repository with mode `0600`.

## Dashboard and brain-transfer boundary

The dashboard API binds only to loopback and accepts browser requests only from the local dashboard origins. It does not expose raw SQLite, arbitrary filesystem paths, or arbitrary body methods. Webcam files are served only by validated basename from the ignored photo directory.

USB/file brain imports accept only bounded Kendra Brain JSONL and merge active memories, not biometric identity tables. Wi-Fi brain retrieval uses batch-mode SSH with strict host-key checking, a validated host/user, and a fixed remote export command. It does not accept passwords or model-proposed shell commands.

## Raw sensor privacy

The design keeps raw microphone audio and camera frames local unless a specific photo delivery action is explicitly requested through an approved alias. Online research sends text queries/page requests, not camera/audio recordings.
