# Home deployment preflight: preparation, not deployment

A public checkout, a local development checkout, and an installed application
can contain different software even when their version strings match. Never
replace an installation from an unreviewed or unrelated checkout.

## Read-only inventory

The optional preflight reads an operator-controlled, ignored profile at
`var/vms-home.json`. Fill its paths with local absolute paths; do not commit the
profile or put credentials in it:

```json
{
  "primary_repo": "/absolute/path/to/current/source",
  "pr_repo": "/absolute/path/to/reviewed/candidate",
  "ssh_host": "user@host.example",
  "installed_gui": "/absolute/path/to/installed/application.py"
}
```

Protect the profile with `chmod 600 var/vms-home.json`. Reuse a pinned SSH
host/key configuration. Do not disable host checking or put passwords in the
profile. Run `make vms-preflight` to report checkout revisions, dirty/clean
state, source fingerprints, installed package version, and configured service
state. The command is read-only: it does not upload, patch, install, restart,
read credentials, or deploy. A successful inventory is not deployment
permission or readiness.

## Gates before a real deployment

1. Reconcile any difference between installed, primary, and candidate source.
   A narrowly reviewed backport and a public PR may be different artifacts.
2. Preserve a clean, reviewed candidate and capture its exact revision and
   source fingerprint. Do not include unrelated dirty work.
3. Test the exact candidate in its intended environment. The current preflight
   does not apply changes or create a PR.
4. Require a separate, explicit approval bound to the reviewed revision and
   installed baseline. A configured GitHub login alone does not provide this
   approval workflow.
5. Before deployment, retain a rollback copy and use a narrow update procedure
   that preserves application data and unrelated services.
6. Verify health and the visible change. Roll back on failure and record the
   exact result.

No deployment step is currently available as a telephone tool. Text delivery
and outbound callbacks also require separate services and authorization; an
inbound phone route does not prove either capability.
