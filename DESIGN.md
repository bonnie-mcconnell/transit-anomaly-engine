# Design notes: NX1/NX2 delay tracker

## Scope

NX1 (`NX1-203`) and NX2 (`NX2-207`), both directions. Got these from
`/gtfs/v3/routes` on 25/06, 545 routes total, filtered for "NX". Note
they don't share a route_id suffix (203 vs 207), I guessed NX2-203 first
and it 404'd, so don't assume routes from the same family number
similarly elsewhere.

Picked these two because they run often enough to hit a usable sample
size per stop in a reasonable timeframe (see cold-start below), and NX1
was already showing up in the raw feed during the first poll so I knew
it was actually live data and not a dead/seasonal route.

Not hardcoding to just these two routes anywhere. route_id is a normal
column, full feed gets pulled every time, filtering happens after
parsing. So scope = config, not a rewrite, if I want more routes later.

## Data source

`https://api.at.govt.nz/realtime/legacy/`, AT's "compat" layer
over GTFS-RT, not whatever their newest API might be. Rate limit is
600/min, 35k/week, way more than needed even at the survey-phase 90s
interval, let alone the 180s production interval (see Ingestion below).

## What the feed actually looks like

Spec says stop_time_update is a repeated field (list). In practice it's
always a single object. Checked across ~23k samples, only missing on
cancelled trips, never a list. Parser should expect a dict and not
crash if it ever isn't one, but I didn't build heavy handling for a
case I haven't seen yet.

trip-level delay and stop-level delay are different numbers, not the
same value duplicated. Saw a trip at delay=64 with its stop update
showing departure delay=107 on the same poll. Storing both, both
nullable.

schedule_relationship is mostly 0 but the non-zero rate moves around a
lot depending on time of day. Afternoon survey: 23057 normal, 24
skipped, 14 cancelled out of ~23k, roughly 1 in 600. Rush hour survey:
121656 normal, 1804 cancelled, zero skipped, out of ~123k, roughly 1 in
68. Cancellation rate at rush hour was about 24x the afternoon rate.
Don't know yet if that's a real pattern (more disruption at peak,
which would make sense) or just one unusual morning, need more days of
real data before treating either number as the rate. Either way,
cancelled trips show delay=0 at the trip level but that's not a real
"on time," it's just an empty field on a trip that isn't running.
Filtered out before it touches anything statistical, not treated as a
normal zero. ingest.py nulls this out at write time now rather than
leaving it as a misleading 0 in the database.

## Extreme delay values

First survey (afternoon, 24/06), n=49732 delay samples:

p50: 95s
p75: 224s
p90: 364s
p95: 464s
p99: 707s
p99.9: 1984s
max: 66464s

Second survey (rush hour, 29/06, 7:18-8:48am), n=258157 delay samples:

p50: 116s
p75: 259s
p90: 455s
p95: 614s
p99: 929s
p99.9: 3425s
max: 6442s

Originally I guessed that the afternoon survey's huge gap between p99.9 and
max was a midnight rollover bug, trip start_time near 00:00 and the
date field off by a day. That guess was wrong. The rush hour survey's
extreme samples logged full trip detail and none of them have
start_times anywhere near midnight, they're all normal morning trips
(07:43, 07:50, 06:25, 07:15, 07:20).

What's actually in the extreme tail is a mix of at least two different
things:

One trip (route 917-203) showed delay climbing steadily poll over poll
for 20+ minutes straight, 3605s up to 5231s and still rising when the
survey ended. That's a bus actually falling further behind in real
time, not bad data. This is the kind of observation this project should surface and not filter out.

Two other trips (route 101-202 and S007F-203) showed large persistent
negative delays, around -6300 to -6450s, flat for many polls, then the
trip just disappears from later polls entirely. Checked one of these
against the static schedule (trip 1254-10101-27780-2-2a40f4a2, route
101-202): scheduled first stop departure is 07:43:46, and the realtime
feed's start_time for the same trip is 07:43:00, so the trip_id and
schedule line up fine, this isn't a lookup error or a rollover. Best
guess now is a vehicle/trip matching problem on AT's side, a vehicle
got assigned a trip_id it isn't actually running, or there's a GPS/
odometer fault making the system think the vehicle is further along
than it is. Haven't fully nailed this down and don't think it's worth
more time right now, just noting it's confirmed not the midnight
theory.

Given this, treating anything over 3600s as garbage to drop was the
wrong model. The new approach is to still flag anything over 3600s as is_extreme so
it doesn't quietly pollute baseline stats, but keep it in the raw
table and surface it, since at least some of these are the real
events the whole project is supposed to catch. The negative, vanishing
kind might actually be noise, the climbing kind clearly isn't, and a
flat threshold can't tell them apart on its own. Worth coming back to
once there's a real backlog of these to look at trip-by-trip rather
than guessing from a couple of examples.

## Unit of analysis

Storing at stop level, one row per trip per stop per poll. Not trip
snapshots, as snapshots of the same trip a couple minutes apart
aren't independent. If a bus is 3 min late at one stop it's probably
still ~3 min late two minutes later, so treating those as separate
samples for a baseline would make the variance look smaller than it
actually is. Different stops, different trips, different days don't
have that problem.

Still keeping trip-level delay on each row since it comes free in the
payload, and that's what drives the "right now" number on the dashboard,
derived from the stop-level model rather than its own thing.

## Buckets and cold start

Weekday/weekend split only for now, no per-weekday breakdown yet,
don't have the volume to support that split without the estimates
getting noisy.

Tested bucket size against synthetic data before waiting on real
collection to find out the hard way. Generated fake stop events shaped
like the real measured percentiles (see generate_synthetic.py), 18
days, both routes, 8 stops each. At 30-min buckets: 2201 total cells,
median n per cell was only 6, and just 272 of them (12.4%) cleared
N=20. At 60-min buckets: 1156 cells, median n=11, 354 cleared N=20
(30.6%).

So 30-min buckets were too fine for how this data actually spreads
out, most cells would sit at "not enough data yet" for a long time.
Switching the default to 60-min buckets. Loses some time-of-day
resolution (can't tell 8:00 from 8:30 apart anymore) but gets useful
output much sooner. This was tested on synthetic data built from
guessed parameters though, not real NX1/NX2 traffic, so the actual
numbers could land differently once there's real data to check against.
Worth re-running this same comparison once a real week or two of
ingestion has built up, the synthetic generator's trip counts and hour
weighting are rough guesses, not measured.

Minimum N=20 observations per (route, direction, stop, day_type, bucket)
cell before showing a real status. Below that it just says "not enough
data yet." Haven't stress tested whether 20 is actually the right
number yet either; its just a starting point.

## Status logic

Showing percentile rank within the historical distribution for that
cell, not a Z-score, because delay isn't close to normal, it's
skewed and AT's own docs say it goes negative pretty often (buses
running early). A Z-score assumes a shape this data doesn't have.

Tiers (picked by feel not derived from anything):
< 75th percentile: normal
75-95th: running late
> 95th: significantly delayed
N < 20: not enough data

Quartiles for the IQR use nearest-rank indexing (sorted_delays[n//4] and
sorted_delays[3n//4]) rather than linearly interpolated quantiles. Chosen
to avoid a numpy dependency for something this simple, and the gap
between the two methods is negligible once n clears MIN_N=20. This means
I expect the IQR figures in the README won't reproduce bit-for-bit if someone
cross-checks them with numpy.percentile's default method.

## Ingestion and architecture

ingest.py does one poll and exits, triggered every 3 minutes by
Windows Task Scheduler. Went with single-shot plus external scheduling
instead of a long-lived process because it survives sleep/wake and
doesn't need a terminal window open for days.

The database is Supabase Postgres in production, with the same schema
accessible locally via SQLite for development and testing. db.py
handles this: if DATABASE_URL looks like a postgres URL it uses
psycopg2, otherwise treats the value as a SQLite file path. Nothing
else in the codebase imports sqlite3 or psycopg2 directly.

Baseline computation runs nightly on GitHub Actions (materialise.py),
not on the laptop or on Render. GitHub Actions is free for public
repos and doesn't require any always-on server for a scheduled job.

The dashboard runs on Render's free web service. Free web services on
Render spin down after 15 minutes of inactivity; a separate GitHub
Actions workflow pings the URL every 10 minutes during Auckland waking
hours to keep it alive.

As a known gap, when the laptop is fully off or off wifi, ingest polls are
missed. Task Scheduler's "run as soon as possible after a missed start"
catches most short gaps but not multi-hour overnight ones. This is
documented, accepted, and shows up visibly in poll_log.

init_schema() runs under a Postgres advisory lock (arbitrary key, see
db.py). ingest.py and materialise.py both call it on every run and can
genuinely overlap. Found by actually running 15 concurrent processes
against a fresh database rather than reasoning about the SQL by eye:
CREATE TABLE/INDEX IF NOT EXISTS is not safe under concurrent first-time
creation, and it doesn't fail with a friendly "already exists" - 11 of
15 failed with a raw unique_violation (23505) on internal catalog
indexes (pg_type_typname_nsp_index, pg_class_relname_nsp_index), since
the IF NOT EXISTS check and the actual catalog insert aren't atomic
together. A first attempt patched two of the affected statements
individually; that was incomplete, since the unguarded CREATE TABLE
itself raced too, and a failure there aborts the transaction before the
later statements even run. The advisory lock around the whole block
fixed it: retested with 15 concurrent processes, 0 failures. Practical
impact right now is low since the Supabase tables already exist and
this only matters on a genuinely fresh database, but it's the kind of
thing a future Postgres integration test would hit.

## Not decided yet

- how long to keep raw rows before pruning to aggregates
- whether the is_extreme flag should influence the dashboard display
  differently once there are enough extreme rows to look at as a group
- terminus stops: the current model doesn't know which stops are route
  endpoints, so a bus arriving early at its final stop looks like
  "running early" rather than being filtered out. Worth fixing by
  pulling stop sequence data from the static API, but not urgent.

## Real findings so far (updated 05/07)

First real aggregate.py run against transit.db after ~4 days of
collection (29,595 rows, 569 successful polls, 204 cells over N=20).

One thing that jumped out immediately: a cluster of NX2 dir=0 cells
at stop 7147-4e9003b4 showing median delays of -400s to -550s across
every time bucket. Looked it up, it's "Stop E Auckland Universities",
the city-centre terminus for NX2. A bus arriving consistently early
at its final stop isn't the same thing as a bus arriving early at a
mid-route stop where a commuter is waiting. At a terminus, early
arrival just means the driver made good time. This stop should
probably be excluded from the status display, or treated differently,
since the "running early" signal isn't actionable there.

This is a real limitation: the current model doesn't know which stops
are terminus stops. Worth fixing later by pulling route stop sequence
data from the static API, but not blocking anything for now.

The actually interesting finding is that NX1 dir=1 (northbound, away from
the city toward the Shore) shows a consistent delay pattern at busway
stations during afternoon peak. At bucket=17 (5-6pm Auckland time):

  Constellation (Stop B):  median +504s (~8.4 min late), IQR [408, 572]
  Akoranga (Stop B):        median +433s (~7.2 min late), IQR [355, 474]
  Smales Farm (Stop B):     median +420s (~7.0 min late), IQR [305, 529]
  Sunnynook (Stop B):       median +384s (~6.4 min late), IQR [260, 460]

These are all Northern Busway stations in northbound order, and delay
increases progressively from south to north, which is what accumulated
congestion along a route looks like. The pattern worsens from bucket=16
to bucket=17 (4-5pm vs 5-6pm), consistent with delay building as the
peak deepens rather than random incidents.

The tight IQRs on these cells (e.g. Constellation's IQR of [408, 572]
means half of all observed trips fell between 7 and 9.5 minutes late)
mean this is a repeatable structural pattern, not noise. A commuter
catching the NX1 northbound at Constellation at 5pm should reliably
budget an extra 8 minutes.

City-end stops (Fanshawe St/Victoria Park, Lower Albert) show much
lower median delays at the same time buckets, consistent with the bus
starting each trip roughly on schedule and accumulating delay along the
route rather than starting late.

Also found and fixed a timezone bug in aggregate.py and check_health.py: polled_at is stored UTC, Auckland is UTC+12 in
winter, so every cell was being bucketed 12 hours wrong. Fixed with
zoneinfo. The raw data in transit.db is fine, the error was only in
the analysis layer.