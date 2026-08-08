# CLINK for LNbits

Nostr-native Lightning payments for LNbits, built on
[CLINK](https://github.com/shocknet/CLINK) — the Nostr-native Lightning standard
using offers (`noffer1...`) and wallet debits (`ndebit1...`).

## Features (v0.1.0 - coming)

- **Offers** — publish a `noffer1...` and receive Lightning payments over Nostr
- **Pay offers** — request and pay invoices from remote CLINK offers
- **Subscriptions** — native subscription engine with auto-renew via debits
- **Wallet Debits** — node service that draws payments from wallets over CLINK
- **CLINK as funding source** — run this LNbits instance backed by CLINK

## Development

```bash
# install dev tooling (ruff, black, pytest, mypy)
uv sync

# lint + format
make

# run tests
make test
```

## License

GPL-3.0. See [LICENSE](LICENSE).
