---
title: Laptop FRP deployment
type: feature
created: '2026-10-20'
status: built
route: full
route_source: pinned
review: quick
review_source: auto
context: []
baseline_revision: d745e4027577cb90b5288b6f339a5095a75c51d7
---
<frozen-after-approval reason="user approved dog deployment proposal">
## Intent
Provide one laptop import/deploy command after cloning MC, using a private laptop bundle and the existing packager. Preserve laptop history and token and keep desktop unchanged.
## Boundaries & Constraints
Use laptop identity, external tunnel mode, local 18080 and remote 19081. No secrets in Git, global proxy changes, Defender changes, automatic termination of daily MC, or duplicate identity. User exits old MC before packaging. Server adds only the laptop FRP port and Caddy route.
</frozen-after-approval>
## Code Map
- `deploy/mc-relay/`: deployment files.
- `package_mc.ps1`, `zgwd.spec`: onedir build.
- `remote_machine_config.py`: persisted pair/domain/token.
## Tasks & Acceptance
- [x] Add laptop import and hidden startup scripts, examples and short instructions.
- [x] Require NATS binary before and after packaging.
- [x] Update MC and RC connection documentation.
- Given a complete external laptop bundle and an exited MC, when deploying, then build in place, retain existing history/token, configure laptop external startup and one FRP process.
- Given missing files, conflicting identity or a running MC, when deploying, then fail before modifying installation data.
- Given repeated deployment, when the original laptop config is present, then retain its token and history.
## Implementation Notes
Implementer directly executes the already approved dog proposal under director assignment; root owns review and independent engineer verification.
## Verification
Parse PowerShell, run affected packaging tests and isolated setup validation. No daily MC or physical phone operations.

Implementation verification: PowerShell parser passed for setup/start/package; packaging spec suite 18 passed. Independent engineer verification pending. Server verified laptop port and Caddy route installed and both services active. No laptop process, physical phone or daily MC modified.

Independent engineer verification: 18 packaging tests passed; PowerShell AST parsed; isolated identity/token rejection and unchanged history verified; launcher laptop/external and child-only proxy handling verified. Server services active, desktop authenticated local/public PONG same identity, forum 200; laptop 502 expected until target laptop connects. Target-laptop real deployment and login validation remain pending.
