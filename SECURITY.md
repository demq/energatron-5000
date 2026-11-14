# Security

## Reporting a vulnerability

Please report it privately, through GitHub's **Report a vulnerability** button on this repository's Security tab
(a private security advisory), not in a public issue. Say what is affected and how to reproduce it, with synthetic data
only. The maintainer will reply as soon as they can.

## What the tool is designed to protect

- Your data stays on your machine. The tool fetches public retail offers from the Consumer Data Right APIs and sends
  nothing about you: no identifiers, no usage, no postcode (the postcode filter is applied locally).
- Identifiers live in `private/config.toml`, which DVC tracks and git never sees. A pre-commit privacy guard blocks
  commits holding them, and every command's output is redacted against them.
- `energatron diagnose` reports structure, never values.

A way around any of these (a path that commits an identifier, output that leaks one, a request that carries one) is a
vulnerability; please report it as above.
