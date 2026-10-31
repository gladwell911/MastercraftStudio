---
title: 'Keep MC and ControlTower FRP forwarding together'
type: 'bugfix'
ticket: ''
created: '2026-10-30'
baseline_revision: '9aa2e44f14b2a5f52173ec3c09d2695a67dd6378'
status: 'built'
route: 'full'
route_source: 'auto'
review: 'thorough'
review_source: 'auto'
lenses_ran: ['blind-hunter', 'edge-case-hunter', 'verification-gap', 'intent-alignment']
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** MCRelay identifies every process using its frpc.exe as its own, so ControlTower can suppress MC startup. MC lacks a persistent child supervisor; concurrent wrappers can also duplicate children.

**Approach:** Keep two independent configurations and tasks. Deploy a small shared PowerShell supervisor with configuration-specific ownership, lifetime locking, recovery, precise stopping and repeatable installation; restore MC public access.

## Boundaries & Constraints

**Always:** Preserve MC 18080→19080 and forum 4200→19420 through frps7000; preserve ControlTower 18679→19679 through frps7001. Retain CA, tokens, proxy names, TLS verification and current-user login startup. Preserve existing work. User authorizes deployment and controlled FRP recovery checks.

**Never:** Restart daily MC, ControlTower API, WSL, shared server services or Cloudflared; change phone data/settings or credentials; bulk-kill by executable/name; modify the dirty ControlTower source tree; claim unattended boot availability. No commit/push without separate instruction.

## I/O & Edge-Case Matrix

| State | Action | Expected | Failure handling |
|---|---|---|---|
| Same EXE, distinct configs | Start both | One child per config | Other profile unaffected |
| Same profile starts concurrently | Acquire lifetime lock | One supervisor/child | Duplicate exits harmlessly |
| Owned child exits | Supervisor waits | Restart after short delay | Bounded backoff, redacted error |
| Supervisor dies, child survives | Task supplements startup | Adopt exact child; no duplicate | Multiple matching children fail closed |
| Stop/update profile | Stop supervisor then matching child | Zero owned children | Recheck PID/create time/config before stop |
| Invalid config/access denied | Start/recover | No guessed ownership | Report failure; never kill others |

</frozen-after-approval>

## Code Map

- Baseline MC main: `9aa2e44f14b2a5f52173ec3c09d2695a67dd6378`, clean before plan.
- `%LOCALAPPDATA%/MCRelay/start-frpc.ps1:4` uses EXE-only detection; then one child and WaitForExit. Actual MCRelay task returned zero while no MC tunnel existed.
- `%LOCALAPPDATA%/controlTower/relay/start-frpc.ps1` uses shared MCRelay/frpc.exe with its own config and five-second restart loop, without instance locking.
- Both tasks: interactive logon, IgnoreNew, unlimited duration, battery allowed, one-minute failure retry. Only ControlTower currently has StartWhenAvailable.
- `main.py:_ensure_cloudflared_tunnel` external mode only checks origin. `package_mc.ps1:145` updates package, not LocalAppData relay; leave both unchanged.
- `deploy/mc-relay/start-laptop.ps1:17` has related EXE detection but a different install/MC lifecycle. Do not deploy laptop changes in this desktop repair.
- `D:/code/controlTower` is the real source repository; its `docs/deployment-frp-20261026.md` specifies precise config-owned stop. Existing dirty changes stay untouched.

## Tasks & Acceptance

**Execution:**
- [x] `deploy/mc-relay/frpc-supervisor.ps1` — implement Run/Stop/Status using absolute normalized config identity and fixed executable path. Parse argv with Windows rules, not substring matching. Hold a per-user/config cross-session lock throughout Run; handle abandoned locks. Search/adopt one matching child under lock; retain Process object and creation identity. Child startup hides windows and removes proxy variables only in child environment. Recover all child exits; catch failures without busy loops. Log state/errors without command lines or secrets.
- [x] Same file — Stop first disables automatic relaunch for the maintenance interval by stopping its exact task, waits for wrapper exit, then revalidates and stops only matching child. Serialize stop/update against Run using the same identity lock. Status exposes only safe identity/state. Do not assume stopping PowerShell stops its child.
- [x] `deploy/mc-relay/install-frpc-supervisor.ps1` — explicit MCRelay/ControlTower profiles, preflight existing config with frpc verify, install helper plus a minimal start-frpc wrapper into each runtime directory. Preserve configs/CA/executable/ACL. Register independent existing task names with login + one-minute supplemental trigger, IgnoreNew, StartWhenAvailable, unlimited duration, battery allowed and existing failure retry. Use exact known executable paths; do not rewrite configuration.
- [x] `tests/test_frpc_supervisor.ps1` — isolated fake child/configs prove all matrix cases, including path spaces/case/slashes, quoted -c, orphan adoption, duplicate refusal, zero/nonzero exit, clean stop and configuration-specific ownership. Test supervisor supplementation without touching daily tasks.
- [x] `deploy/mc-relay/README.md` — document source-of-truth installer, independent identities, precise maintenance/restart and login boundary. Avoid a shared binary upgrade while either profile runs. Keep laptop outside live rollout.
- [x] Deploy both profiles with require_escalated. Preflight current state again; preserve safe task XML/script backups outside repositories. Stop only ControlTower FRP wrapper/child during wrapper replacement; its API remains running. Install both, start tasks, restore MC. On failure restore affected scripts/task only; preserve the other relay and forum configuration.
- [x] `docs/handoff.md` and plan verification section — record actual deployment, evidence and residual limits; no secret logs/configs in Git.

**Acceptance Criteria:**
- Given daily applications stay running, when either relay starts first or starts concurrently, then both configured proxies register and application PIDs remain unchanged.
- Given one owned FRP child is terminated in an authorized recovery check, when its supervisor observes exit, then only that profile recovers and the other stays available.
- Given supervisor completion/crash, when supplemental task fires, then exactly one child per configuration remains.
- Given successful deployment, when authenticated MC probes compare direct/bridge/public PONG, then server IDs match and the real phone connects; ControlTower read-only API returns valid HTTPS success and forum proxy remains usable.

## Implementation Notes

Planning authorization: user explicitly requested dog to plan, then director to implement, and subsequently said continue. This authorizes the handoff without another routine approval. No implementation has run. Director must re-read this plan before implementation.

Runtime wrappers are owned by the MC installer. Installer/status must record and check deployed script hashes; refuse silent overwrite of externally changed wrappers. ControlTower historical deployment instructions can recreate the old wrapper: document that future relay setup must use this installer, and expose drift in Status. This repair does not modify the dirty ControlTower repository; source updates there remain deferred.

## Plan Change Log

## Review Triage Log

Review execution: two context-free review agents ran; thread capacity prevented two more fresh agents, so dog performed verification-gap and director supplied explicitly labelled implementer intent self-audit. No model overrides. Main agent read source and actual matrix output before triage.

| ID | Verdict | Route | Evidence / action |
|---|---|---|---|
| Blind1 | medium | patch | Resume removes maintenance/enables task without transition lock while Stop can still kill child. Serialize demonstrated transitions. |
| Blind2 | medium | patch | Installer rollback unconditionally removes original marker. Snapshot and restore original maintenance bytes/task state. |
| Blind3 | medium | patch | Resume only queues scheduled launch; installer reports success without child readiness. Add bounded readiness check/rollback. |
| Blind4 | medium | patch | Main verified deployed frpc accepts -c=absoluteConfig with verify exit0; parser does not recognize it. Add exact option form and regression. |
| Blind5 | false | reject | Relative argv identity is deliberately unavailable; fail-closed error preserves no-guessed-ownership invariant. Guessing a process working directory could adopt or stop another profile. Current managed wrappers use absolute paths. |
| Blind6 | medium | patch | Empty Hashes enumerates no required entries and returns clean. Validate required schema and recorded identities. |
| Blind7 | medium | patch | Existing fake-child suite never invokes installer/task registration or rollback. Add isolated failure/maintenance restoration coverage. |
| Edge1 | medium | patch | Installer has no transaction lock; concurrent runs can replace/revert each other's scripts. Serialize per-profile install/maintenance transitions. |
| Edge2 | medium | patch | Resume suppresses marker deletion errors then enables task. Require successful deletion first. |
| Edge3 | medium | patch | Same original maintenance-state destruction as Blind2; preserve bytes and task enabled/running state. |
| Verify1 | medium | patch | Pre-verified gap: rollback byte/state restoration not exercised. Add injected registration/startup failures on isolated fixtures. |
| Verify2 | medium | patch | Pre-verified gap: only unmanaged status tested. Add real manifest clean/drift/corruption and Run/install refusal tests. |

Intent self-audit: diff implements independent supervised forwarding; real phone acceptance remains blocked by absent ADB device, ControlTower HTTP success by pre-existing API origin without port mapping. These do not establish an FRP ownership regression; retain explicit limitations. Source/deployed whitespace difference will be eliminated on patch redeployment.

## Design Notes

Two configurations are required by existing independent frps ports/CA/auth. Sharing a binary is valid; configuration identity must govern ownership. Source all common tooling in the clean MC repository and deploy copies to both runtime directories; avoid introducing a new service platform or changing dirty ControlTower source. Estimate exceeds 100 lines, so route is full and director implements.

## Verification

- Parse new PowerShell scripts and run the isolated supervisor test with Windows PowerShell 5.1; all matrix assertions pass, no daily processes used as fixtures.
- Run frpc verify separately for both unchanged configs, export tasks and confirm actual triggers/settings.
- Capture application PIDs before/after; inspect exact-config child counts and safe logs; exercise recovery one profile at a time and verify other route.
- Using existing private tokens only in memory, complete MC direct18081/bridge18080/public NATS authentication and PONG/server-ID comparison. Confirm actual phone main package/connection, without sending a model request.
- Read-only ControlTower API and forum GET checks; no business writes. Follow web-access skill for network requests, no Chrome required.
- `git diff --check` plus targeted changed-file review. Do not run unrelated Python/Flutter suites or repackage daily MC.
### Actual implementation verification — 2026-10-30

## 2026-10-30 desktop FRP independent supervision

Source tooling: `deploy/mc-relay/frpc-supervisor.ps1` and `install-frpc-supervisor.ps1`; runtime copies and minimal wrappers are installed for `MCRelay-frpc` and `ControlTower-frpc`. Both use exact normalized config/Windows argv ownership with a per-user/config Global lifetime mutex, orphan adoption, bounded child recovery, maintenance stop/resume and deployed hash drift detection. Future desktop relay setup must use this installer; historical ControlTower instructions can replace the wrapper incorrectly. ControlTower source remains untouched; laptop rollout is excluded. No commit/push or package rebuild performed.

Both unchanged configs passed `frpc verify`. Actual tasks have current-user interactive logon plus one-minute supplemental trigger, IgnoreNew, StartWhenAvailable, unlimited duration, battery operation and existing one-minute/999 failure retry. Runtime script/task backups are outside Git under `%LOCALAPPDATA%/MCRelay-supervisor-backups/20261030-230838-820` (original) and `20261030-230932-097` (successful task-trigger update). The first attempted hand-built trigger XML was rejected; the system task-trigger API replaced that implementation, and both tasks were successfully registered and inspected afterward.

Windows PowerShell 5.1 isolated fake-child matrix passed in full, including spaces/case/slashes, quoted config, same-EXE independent configs, concurrent wrapper, child-only proxy cleanup, orphan adoption/abandoned lock, duplicate refusal, zero/nonzero exits, precise stop/maintenance suppression, missing config/access denial and safe Status. Actual controlled recovery: MC child 30100→14776 with ControlTower 17404 unchanged; ControlTower 17404→15888 with MC 14776 unchanged; MC wrapper 29284 was terminated and the scheduled supplement adopted surviving child 14776. Each config finished with one child and a running task.

Authenticated direct18081/bridge18080/new-public `wss://mc-desktop-test.tingyou.cc:8443/nats` all returned PONG with identical server ID; `https://forum-dev-test.tingyou.cc:8443/` returned HTTP200 with normal certificate verification after recovery. Earlier probes against historical rc/tingyou endpoints included transient timeouts and are not evidence for the new FRP path. Daily MC PID7480 and creation time remained unchanged, and both config SHA256 values were identical before/after. Tokens were read only into probe memory; no model request, business write, phone setting change, CA/token/config rewrite or shared service restart occurred.

Remaining acceptance limits: ControlTower FRP successfully logs in/registers `controltower-api`, but public HTTPS returns502 because unchanged `127.0.0.1:18679` refuses connection; existing `ctkeepall_498eae7dc5-api-1` container is present without a host port mapping. API restoration/port publishing needs separate authorized service work; this repair did not restart it. Existing SDK ADB server5037 is alive but reports no devices, so actual phone main-package/connection acceptance remains unverified. Login startup is configured, not unattended boot availability or a reboot test.
Private actual outputs: `C:\Users\gladwell\AppData\Local\MCRelay-supervisor-evidence\20261030-230836` (`isolated-tests.txt`, `before.json`, `after.json`, `recovery.json`, `probes-frp-final.json`). Probe scripts there read existing credentials at runtime; no secrets/configs/raw response bodies enter Git.

### 2026-10-30 review fixes and redeployment

Added operation locking across Stop/Resume and installer preflight/success/rollback, strict managed-manifest identity/hash validation, fail-closed Resume marker removal, -c= ownership, exact maintenance/task-state rollback and bounded installation readiness. Both targeted Windows PowerShell5.1 suites passed (`tests/test_frpc_supervisor.ps1` and `tests/test_frpc_installer.ps1`), covering concurrency, normal/maintenance/managed rollback, startup failure, drift refusal and marker-removal failure. No production test parameters were added.

Redeployed both profiles via installer; private backup `%LOCALAPPDATA%/MCRelay-supervisor-backups/20261030-232704-694`. Final source/helper runtime bytes match exactly; both manifests clean, tasks enabled/running, exactly one child per config (MC20932, ControlTower20904). Both config hashes and daily MC PID7480/creation time stayed unchanged. Final direct/bridge/FRP-public PONG server IDs match and FRP forum HTTPS200/TLS verified. Existing ControlTower API/phone acceptance limits above remain; neither was restarted or changed. Actual output directory: `C:\Users\gladwell\AppData\Local\MCRelay-supervisor-evidence\review-fixes-20261030-232443` (`supervisor-tests.txt`, `installer-tests.txt`, `deployment.txt`, `status.json`, `before.json`, `after.json`, `probes-frp-final.json`).

### Main-agent final audit

All retained review patches were completed. Main independently ran both isolated suites after the fixes: ALL MATRIX TESTS PASSED and ALL INSTALLER TESTS PASSED, both exit0. Every original matrix row is covered by executed assertions. Main read the changed supervisor, installer and tests and confirmed both real tasks Running, exactly one owned child each, active supervisors, no maintenance, clean deployment and source/deployed helper SHA256 equality. git diff --check passed. Independent blind/edge findings were triaged individually; review resource limits and reused verification/self-audit context are recorded above.

Built refers to FRP ownership/recovery implementation and deployment. Actual phone acceptance remains unavailable while ADB reports no devices. ControlTower FRP is registered, but its pre-existing API origin has no host18679 listener; API success is not claimed. No local commit/push per approved plan constraints.
