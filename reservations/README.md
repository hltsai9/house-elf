# reservations — book a table at Din Tai Fung Scottsdale

A small house-elf errand: get you a table at **Din Tai Fung, Scottsdale Fashion
Square**. Pure Python stdlib, zero installs, runs offline by default.

```bash
# what's open in my window?
python -m reservations check --party 2 --day 2026-07-06 --from 11:00 --to 13:30

# book the best slot — and if it's full, join the waitlist automatically
python -m reservations book  --party 2 --day 2026-07-06 --from 11:00 --to 13:30 \
    --name "Ada Lovelace" --phone 480-555-0100
# → ✔ Reserved 2 @ Mon Jul 06 11:30 · mock · conf DTF-30656550
```

## How booking Din Tai Fung Scottsdale actually works

DTF Scottsdale (7014 E Camelback Rd, Suite 608; ☎ 480-256-8683) runs entirely on
**Yelp Guest Manager**. There are two doors, and the tool models both:

1. **Reservations** — `yelp.com/reservations/din-tai-fung-scottsdale-5`. Tables
   release in a batch **~30 days ahead at midnight local** and are gone within
   *seconds*. Refreshing at 11:59pm and racing the drop is the only way to land
   one; the page hands off to Yelp, which holds your slot behind a credit card.
2. **Waitlist** — `yelp.com/waitlist/din-tai-fung-scottsdale-5`. Join remotely
   before you arrive, get a quoted wait + a text when the table's ready. This is
   the realistic walk-up path; the quote is **shortest right at open**. The whole
   party must be present to be seated.

(There's no public official API, and Yelp gates reservation *write* access to
approved partners — so a phone call to the restaurant, or an Amex Platinum/
Centurion concierge, are the human fallbacks people actually use.)

The tool encodes that reality: **try to reserve, fall back to the waitlist**, and
for the midnight drop, **camp on it** until a slot appears.

## Commands

```
python -m reservations check     ...   # list open slots in your window
python -m reservations book      ...   # reserve best slot, else join waitlist
python -m reservations watch     ...   # poll for a drop, grab the first slot
python -m reservations waitlist  ...   # join the remote waitlist directly
python -m reservations info             # the booking facts above
```

Shared flags: `--party N --day YYYY-MM-DD --from HH:MM --to HH:MM [--at HH:MM]
--name "…" --phone … [--email …] [--notes …] [--provider mock|yelp]`.

The booker picks the open slot **closest to `--at`** (defaults to `--from`)
inside your `[--from, --to]` window.

### Camp on the midnight drop

```bash
python -m reservations watch --party 4 --day 2026-07-04 --from 18:00 --to 19:30 \
    --name "Ada Lovelace" --phone 480-555-0100 --attempts 40 --interval 1
```

`watch` polls openings with exponential backoff and reserves the first acceptable
slot the instant it appears; only if the whole hunt comes up empty does it fall
back to the waitlist.

## Email me when a slot opens

Any command takes `--notify {none,console,email}`. `console` prints the message
(handy for dry runs); `email` sends it over SMTP. You get a note when a table is
**booked/waitlisted** (`book`, `watch`, `waitlist`) or when slots simply **appear**
(`check`, and `watch --alert-only`).

```bash
# poll for a midnight drop and EMAIL me the moment a slot appears — don't book it
export SMTP_HOST=smtp.gmail.com SMTP_USER=you@gmail.com SMTP_PASSWORD=app-password
python -m reservations watch --party 2 --day 2026-07-04 --from 18:00 --to 20:00 \
    --name "Ada Lovelace" --phone 480-555-0100 \
    --attempts 60 --interval 1 --alert-only \
    --notify email --email-to you@gmail.com
```

```
● 2 slot(s) opened: 18:30, 19:00 (mock)
  📧 notified: 🍜 2 table(s) open at Din Tai Fung — Scottsdale — Sat Jul 04
```

Drop `--alert-only` to **book and then email the confirmation**. SMTP config comes
from the environment so secrets stay out of argv:

| env var | meaning | default |
|---------|---------|---------|
| `SMTP_HOST` | mail server (required for email) | — |
| `SMTP_PORT` | port | `587` (`465` with `SMTP_SSL=1`) |
| `SMTP_USER` / `SMTP_PASSWORD` | auth | — |
| `SMTP_FROM` | sender | `SMTP_USER` |
| `SMTP_SSL` / `SMTP_STARTTLS` | transport security | SSL off / STARTTLS on |
| `RESERVATIONS_EMAIL_TO` | recipient (or pass `--email-to`) | — |

If email isn't configured, the command still runs the booking and prints a clear
one-line warning instead of failing — so a missing password never costs you a table.

## Providers — swap the backend, keep the errand

The booker depends only on a `Provider` protocol, the same collector/renderer
split the dashboard uses. Two ship in the box:

| provider | what it is | needs |
|----------|------------|-------|
| `mock` *(default)* | deterministic, offline stand-in. Models DTF's reality: weekends + 6–8pm are slammed, big parties are scarcer, off-peak weekday lunch opens up — and there's always a waitlist. Stable across runs, so tests can assert on it. | nothing |
| `yelp` | faithful sketch of the real Yelp Guest Manager flow: `openings → holds → reservations`, plus `waitlist/visit`. | `YELP_API_KEY` (partner-gated) |

Without a key, `--provider yelp` fails *loudly with guidance* rather than
pretending to book:

```
✖ Booking failed · yelp · no YELP_API_KEY set. Yelp gates reservation
  write-access to approved partners. Set YELP_API_KEY, or run with
  --provider mock to dry-run the booking flow offline.
```

## Use it from Python

```python
from reservations import Booker, ReservationRequest, get_provider

req = ReservationRequest.of(
    party_size=2, day="2026-07-06", earliest="11:00", latest="13:30",
    preferred="12:00", name="Ada Lovelace", phone="480-555-0100")

booking = Booker(get_provider("mock")).book(req)
print(booking.summary())     # ✔ Reserved 2 @ Mon Jul 06 11:30 · mock · conf DTF-…
print(booking.outcome)       # Outcome.CONFIRMED | WAITLISTED | UNAVAILABLE | FAILED
```

## Architecture

```
  request ──▶ Booker ──▶ Provider (mock | yelp)
              │            └─ find_openings → reserve → (fallback) join_waitlist
              └─ pick_best: slot closest to preferred, within window
```

| module        | role |
|---------------|------|
| `model.py`    | `ReservationRequest`, `Offer`, `Booking`, `Outcome`, parsing/validation |
| `providers.py`| `Provider` protocol, `MockProvider`, `YelpProvider`, `get_provider` |
| `notify.py`   | `Notifier` protocol, `EmailNotifier`/`ConsoleNotifier`, message composition |
| `booker.py`   | orchestration: `pick_best`, `book`, `watch`, `alert_when_open`; DTF facts |
| `__main__.py` | the CLI |

## Tests

```bash
python -m unittest reservations.tests.test_booking
```
