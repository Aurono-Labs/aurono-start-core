# aurono-start-core

This repo is the open-source trust layer of [Aurono Start](https://auronolabs.com): the code that decides whether a trade fires and how much it moves, published so anyone can read exactly what it does before trusting it with their exchange account.

It is **not** a runnable application. There's no API server, no UI, no way to install this and start trading. It's the deterministic math and event contracts that the full product (`aurono-startV1`, private) is built on top of — sizing, ledger accounting, decision/rejection logic, P&L analytics, and the Lab's Simulate math.

## Why this exists

Aurono Start places real orders on your exchange account based on rules you define. "Trust me" isn't good enough for that. This repo is the part of the codebase where that trust has to be earned: every function here is pure, deterministic, and covered by tests that pin its exact behavior. If a strategy rejects a trade, sizes an order, or a Lab backtest reports a return, the logic that produced that number is sitting right here.

What's *not* here — exchange adapters, credential handling, the UI, deployment tooling — is proprietary. None of it can override or reinterpret what this layer decides; it only calls into it. See [Aurono's open-source boundary](https://auronolabs.com) for the full split.

## Structure

| Path | What it is |
|---|---|
| `aurono/domain/` | Strategy evaluation, decision/rejection logic, core types |
| `aurono/events/` | Event schemas, emit, append-only store |
| `aurono/ledger/` | Capital/inventory ledger, settlement, state rebuild |
| `aurono/analytics/` | P&L, trade statistics, milestones |
| `aurono/execution/sizing/` | Order sizing engine |
| `aurono/execution/commands.py`, `events.py` | Execution contract types |
| `aurono/reports/generator.py` | Report data aggregation (not rendering) |
| `lab-core/` | Lab Simulate math — sigma thresholds, trigger simulation, equity curve, benchmark comparison |
| `tests/` | The tests that pin all of the above |

## How this repo is kept up to date

This is a **content mirror**, updated by a sync job from the private monorepo where Aurono Start is developed. It carries no history from that repo — each sync is a fresh snapshot, so commit messages, branches, and unrelated work never cross over. A standing test (`tests/domain/test_import_boundaries.py`) in the private repo guarantees every file mirrored here never imports anything from the proprietary layers (exchange adapters, credentials, UI, deploy tooling) — the boundary is enforced by CI, not just by convention.

## License

AGPL-3.0. See [`LICENSE`](./LICENSE). The short version: you can read, run, modify, and redistribute this code, including commercially — but if you run a modified version as a network service, you must offer users of that service the modified source. This keeps the trust-layer math itself always inspectable, including by anyone building on top of it.

## Questions

This repo doesn't take feature requests or bug reports for the full product — that's [Aurono Start support](https://support.auronolabs.com). Issues specific to this repo (a discrepancy between documented and actual behavior, a licensing question) are welcome here.
