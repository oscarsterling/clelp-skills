# examples/

Smoke-test outputs from running the two scripts end to end.

## Good path (expected PASS)

From `scripts/demo_spec.json`:

- `demo_banner.png` + `demo_banner.regions.json` (via `compose_banner.py`)
- `demo/desktop_composite.png` and `demo/phone_composite.png` (via `validate_banner.py`)

Copy sits inside the safe band, clear of `avatar_zone`, `mobile_top_crop`,
`mobile_bottom_crop`, and `mobile_icon_cluster`. Validator should exit 0.

## Bad path (expected FAIL)

From `scripts/bad_sample_spec.json`:

- `bad_banner.png` + `bad_banner.regions.json` (via `compose_banner.py`)
- `bad/desktop_composite.png` and `bad/phone_composite.png` (via `validate_banner.py`)

Payoff copy is deliberately parked inside the top-right `mobile_icon_cluster`
danger zone. Validator should exit 1 and name `mobile_icon_cluster` explicitly.

This pass/fail pair is what makes the skill tested, not just listed.

## Reproduce

From the skill root:

```
python3 scripts/compose_banner.py scripts/demo_spec.json examples/demo_banner.png
python3 scripts/validate_banner.py examples/demo_banner.png examples/demo_banner.regions.json examples/demo
python3 scripts/compose_banner.py scripts/bad_sample_spec.json examples/bad_banner.png
python3 scripts/validate_banner.py examples/bad_banner.png examples/bad_banner.regions.json examples/bad
```
