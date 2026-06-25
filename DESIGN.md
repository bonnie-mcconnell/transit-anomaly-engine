# Design notes - NX1/NX2 delay tracker

## Scope

NX1 (`NX1-203`) and NX2 (`NX2-207`), both directions. Got these from
`/gtfs/v3/routes` on 25/06 - 545 routes total, filtered for "NX". Note
they don't share a route_id suffix (203 vs 207), I guessed NX2-203 first
and it 404'd, so don't assume routes from the same family number
similarly elsewhere.

Picked these two because they run often enough to hit a usable sample
size per stop in a reasonable timeframe (see cold-start below), and NX1
was already showing up in the raw feed during the first poll so I knew
it was actually live data and not a dead/seasonal route.

Not hardcoding to just these two routes anywhere as route_id is a normal
column, full feed gets pulled every time, filtering happens after
parsing. So scope = config, not a rewrite, if I want more routes later.

## Data source

`https://api.at.govt.nz/realtime/legacy/` - this is AT's "compat" layer
over GTFS-RT, not whatever their newest API might be. Rate limit is
600/min, 35k/week, which is way more than I need polling every 90s.

## What the feed actually looks like

Spec says stop_time_update is a repeated field (list). In practice it's
always a single object. Checked across ~23k samples, only missing on
cancelled trips, never a list. Parser should expect a dict and not
crash if it ever isn't one, but I'm not building heavy handling for a
case I haven't seen yet.

trip-level delay and stop-level delay are different numbers, not the
same value duplicated. Saw a trip at delay=64 with its stop update
showing departure delay=107 on the same poll. Storing both, both
nullable.

schedule_relationship is 0 almost always. From the first survey:
23057 normal, 24 skipped, 14 cancelled, out of ~23k trip updates total.
So roughly 1 in 600. Cancelled trips show delay=0 at the trip level but
that's not a real "on time," it's just an empty field on a trip that
isn't running - needs to be filtered out before it touches anything
statistical, not treated as a normal zero.

## Garbage delay values

First survey, n=49732 delay samples (trip + stop level combined):

p50: 95s
p75: 224s
p90: 364s
p95: 464s
p99: 707s
p99.9: 1984s
max: 66464s

That jump from p99.9 to max is way too big to be real bus delay. 1984s
(33 min) is believable, a bus can genuinely be that late. 66464s is 18.5
hours, which isn't a delay, it's a bug. My guess is a midnight rollover
thing - trip start_time near 00:00 and the date field off by a day,
matches what the GTFS spec says about service days running past 24:00.
Haven't actually confirmed this yet, feed_survey_v2 logs trip_id/
start_time/start_date on anything over the threshold so I can check
tomorrow.

Quarantine cutoff: |delay| > 3600s gets flagged before it reaches the
stats layer. Picked this because it sits comfortably above the real
p99.9 but well under the garbage values. Logging everything that gets
quarantined rather than just dropping it, in case 3600 turns out to be
wrong and I need to retune it later.

TODO before I trust any of the above for real: run the rush hour survey
(tomorrow, ~7:15-8:45am) and check -
- does the percentile shape hold, or does peak traffic push p90+ up
- does the cancelled/skipped rate change at peak
- do the logged extreme samples actually show the midnight pattern

## Unit of analysis

Storing at stop level, one row per trip per stop per poll. Not trip
snapshots. Reason: snapshots of the same trip a couple minutes apart
aren't independent - if a bus is 3 min late at one stop it's probably
still ~3 min late two minutes later, so treating those as separate
samples for a baseline would make the variance look smaller than it
actually is. Different stops, different trips, different days don't
have that problem.

Still keeping trip-level delay on each row since it comes free in the
payload and drives the "right now" number on the dashboard,
derived from the stop-level model rather than its own thing.

## Buckets and cold start

30-min time buckets. Weekday/weekend split only for now, no per-weekday
breakdown yet because I don't have the volume to support that split without the
estimates getting noisy.

Minimum N=20 observations per (route, direction, stop, day_type, bucket)
cell before showing a real status. Below that it just says "not enough
data yet."

Picked 30 min and N=20 off general reasoning about how much you need
before a median/IQR stops jumping around, not from NX1/NX2 data
specifically - haven't run ingestion long enough yet to check if these
actually make sense for these two routes. Will know more in a few days.

## Status logic

Showing percentile rank within the historical distribution for that
cell, not a Z-score, because delay isn't close to normal; it's
skewed and AT's own docs say it goes negative pretty often (buses
running early). A Z-score assumes a shape this data doesn't have.

Tiers, picked by feel not derived from anything:
< 75th percentile: normal
75-95th: running late
> 95th: significantly delayed
N < 20: not enough data

## Not decided yet

- Postgres locally vs Supabase, haven't set up either
- how long to keep raw rows before pruning to aggregates
- frontend - not touching this until ingestion is actually solid