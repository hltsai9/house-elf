# house-elf

## Mission Control

An ASCII dashboard to oversee teams of AI agents — see
[`mission_control/`](mission_control/README.md). Pure stdlib, zero installs.

```bash
python -m mission_control web --demo    # in your browser  →  http://127.0.0.1:8000
python -m mission_control demo           # or in the terminal
```

## Reservations

Book a table at Din Tai Fung, Scottsdale — see
[`reservations/`](reservations/README.md). Tries to reserve, falls back to the
waitlist, and can camp on the midnight drop. Pure stdlib, runs offline.

```bash
python -m reservations book --party 2 --day 2026-07-06 --from 11:00 --to 13:30 \
    --name "Ada Lovelace" --phone 480-555-0100
```
