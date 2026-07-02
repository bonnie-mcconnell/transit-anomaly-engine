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
crash if it ever isn't one, but I'm not building heavy handling for a
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

Originally guessed the afternoon survey's huge gap between p99.9 and
max was a midnight rollover bug, trip start_time near 00:00 and the
date field off by a day. That guess was wrong. The rush hour survey's
extreme samples logged full trip detail and none of them have
start_times anywhere near midnight, they're all normal morning trips
(07:43, 07:50, 06:25, 07:15, 07:20).

What's actually in the extreme tail is a mix of at least two different
things:

One trip (route 917-203) showed delay climbing steadily poll over poll
for 20+ minutes straight, 3605s up to 5231s and still rising when the
survey ended. That's a bus genuinely falling further behind in real
time, not bad data. This is the most interesting single observation
from the whole survey and the project should be able to surface this
kind of thing, not filter it out.

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
wrong model. New plan: still flag anything over 3600s as is_extreme so
it doesn't quietly pollute baseline stats, but keep it in the raw
table and surface it, since at least some of these are the real
events the whole project is supposed to catch. The negative, vanishing
kind might genuinely be noise, the climbing kind clearly isn't, and a
flat threshold can't tell them apart on its own. Worth coming back to
once there's a real backlog of these to look at trip-by-trip rather
than guessing from a couple of examples.

## Unit of analysis

Storing at stop level, one row per trip per stop per poll. Not trip
snapshots. Reason: snapshots of the same trip a couple minutes apart
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
number yet either, just a starting point.

## Status logic

Showing percentile rank within the historical distribution for that
cell, not a Z-score, because delay isn't close to normal, it's
skewed and AT's own docs say it goes negative pretty often (buses
running early). A Z-score assumes a shape this data doesn't have.

Tiers, picked by feel not derived from anything:
< 75th percentile: normal
75-95th: running late
> 95th: significantly delayed
N < 20: not enough data

## Ingestion

ingest.py does one poll and exits, writes to a local SQLite file
(transit.db), and is triggered every 3 minutes by Windows Task
Scheduler rather than running its own loop. Went with single-shot plus
external scheduling instead of a long-lived process because it
survives sleep/wake and doesn't need a terminal window open for days.

Stores both raw_stop_events and a poll_log table, the second one just
records whether each attempt succeeded and how many rows it wrote, so
gaps in collection (missed polls, laptop asleep, no wifi) can actually
be checked later instead of just assumed away. There's a unique
constraint on (trip_id, stop_id, polled_at) to stop a single poll's
result from inserting the same row twice, but that doesn't fully cover
Task Scheduler double-firing the whole script, since two separate
processes get two different timestamps. Set "do not start a new
instance if already running" in the task's settings for that, the
database can't solve it on its own.

Known gap: if the laptop is fully off or off wifi when a trigger fires,
that poll is just missed, no retry queue. Task Scheduler's "run as
soon as possible after a missed start" helps but won't catch everything.
Not fixing this properly right now, just noting it so any gaps in the
collected data later aren't a surprise.

SQLite chosen over Postgres/Supabase for now. Two routes is not much
data and zero setup beats hosted infrastructure at this stage. Schema
isn't tied to SQLite specifically if this needs to move later.

## Not decided yet

- how long to keep raw rows before pruning to aggregates
- frontend, not touching this until there's a real backlog of data
  to show
- what to actually do with is_extreme rows once there's enough of
  them to look at as a group instead of one at a time

## Real findings so far (updated 03/07)

First real aggregate.py run against transit.db after ~4 days of
collection (29,595 rows, 569 successful polls, 204 cells over N=20).

One thing that jumped out immediately: a cluster of NX2 dir=0 cells
at stop 7147-4e9003b4 showing median delays of -400s to -550s across
every time bucket. Looked alarming. Looked it up, it's "Stop E
Auckland Universities", the city-centre terminus for NX2. A bus
arriving consistently early at its final stop isn't the same thing as
a bus arriving early at a mid-route stop where a commuter is waiting.
At a terminus, early arrival just means the driver made good time.
This stop should probably be excluded from the status display, or at
minimum treated differently, since the "running early" signal isn't
actionable or meaningful there.

This is a real limitation: the current model doesn't know which stops
are terminus stops. Worth fixing later by pulling route shape/stop
sequence data from the static API, but not blocking anything for now.

The actually interesting finding: NX1 dir=1 (southbound, inbound
toward the city) shows a consistent and large delay pattern at
specific stops during afternoon peak. At bucket=17 (5-6pm Auckland
time), several stops show medians of 400-504 seconds (7-8 minutes
late), with tight IQRs suggesting this is repeatable, not random.
The pattern gets worse from bucket=16 to bucket=17, which looks like
delay accumulating as the peak deepens rather than one-off incidents.
Stop names pending lookup (see lookup_stops.py), but the pattern
shows up across at least 4-5 distinct stops on the same run, which
makes it much more credible than a single-stop artifact.

Also found and fixed a timezone bug in aggregate.py and check_health.py
during this session: polled_at is stored UTC, Auckland is UTC+12 in
winter, so every cell was being bucketed 12 hours wrong. Fixed with
zoneinfo. The raw data in transit.db is fine (UTC timestamps are
correct), the error was only in the analysis layer.