# Scout

A personal job recommender built on the idea behind Jobright, for two lanes Jobright doesn't serve:

- **Global remote:** remote jobs someone living in Lagos can actually take
- **Nigeria:** every Lagos job, whether on-site, hybrid or remote, plus remote roles anywhere in Nigeria. On-site and hybrid jobs in other cities are left out

Everything runs on your machine. Your CV never leaves it: matching uses a local embedding model.

## Run it

```bash
uv run scout ingest     # fetch, classify, embed. First run is slow; later runs embed only new jobs
uv run scout serve      # http://127.0.0.1:8787
```

Run `ingest` about once a day. Skipping days is fine: a posting only closes once its board has fetched successfully for 3 days without it, so a missed run or a failing board never empties the list. Postings over 60 days old drop out too. Jobs you liked or applied to stay in those tabs, marked as closed.

In the Applied tab, give each application a status: no reply yet, in progress, rejected or hired. It's only for your own tracking and doesn't change the ranking.

Other commands:

```bash
uv run scout top --lane nigeria -n 20                 # best matches in the terminal
uv run scout audit                                  # what got rejected, and why
uv run scout audit --lane global_remote --scope emea
uv run scout reprocess                              # reclassify after editing the rules
```

To change what it looks for, edit `profile.json` and restart `serve`:

- `primary_titles` are your main direction (frontend). `secondary_titles` still match, but rank lower.
- `low_priority_title_terms` sinks roles with those words in the title. Embeddings rate "Backend Engineer" as nearly the same as "Frontend Engineer", so this needs saying outright.
- `core_skills` is your main stack. Postings that miss it entirely get less skills credit.
- `level_range` is the seniority you'll take, from 0 (intern or trainee) to 5 (director). Everything in the range scores the same.

Skills must exist in `SKILLS` in `scout/matching.py`. To follow more companies, add their slugs to the lists in `scout/sources.py`.

## How it works

```
sources.py      13 public feeds and job boards  →  RawJob
eligibility.py  Can someone in Lagos take this?  →  lane, scope, confidence, evidence
matching.py     How well does it fit the profile? →  score and breakdown
ranker.py       Filter, apply feedback, diversify →  recommendations
```

**Eligibility** is the part that matters most. Most "remote" jobs aren't open worldwide, so:

| Posting says | Result |
|---|---|
| Worldwide, Anywhere, Global, no restrictions listed | Global remote, high confidence |
| Nigeria named, or on a short list of countries, or Africa | Global remote, high confidence |
| EMEA, or a long country list that happens to include Nigeria | Global remote, medium confidence: this often really means Europe |
| Only "Remote" | Unverified, hidden unless you tick the box |
| Named elsewhere: USA, Remote Spain, AMER, South Africa | Rejected |
| "Worldwide" label, but the text says "must be based in the US" | Rejected, quoting the sentence |
| Title names a region: "Sales Rep - DACH" | Rejected |
| "Ideally based in Poland" | Not a restriction, kept |
| "Pay for US-based staff is…" | Not a restriction, kept |
| Overlap with US time zones | Kept, with a warning |
| Time zones that exclude UTC+1 | Rejected |
| Lagos, in any setup | Nigeria |
| On-site or hybrid elsewhere in Nigeria: Abuja, Port Harcourt, plain "Nigeria" | Rejected, as it would mean moving |
| A Nigerian listing whose text says the role is remote | Nigeria, as remote |
| "Remote, Nigeria" or "Remote - Abuja" | Nigeria, because the employer is hiring where you live |

**Score:** title 32%, background 23%, skills 25%, seniority 10%, industry 5%, freshness 5%. Roles in an unrelated profession are damped. "Like", "Applied" and "Not for me" move similar jobs by up to 12 points compared with the rest of the pool, immediately, and clicking again undoes it. After "Not for me", Scout asks why, and the answer decides what moves: *Not open to me* only nudges similar jobs slightly, *Wrong role* sinks similar titles, *Wrong stack* sinks jobs asking for that posting's skills outside your profile, *Too senior* sinks that level and above, and *Not applying* (the job fits, but it's closed, asks too much, or similar) only hides that job and moves nothing else. Without an answer, everything similar sinks. `uv run scout misreads` lists the jobs you marked as not open to you, which is where the eligibility rules need fixing. Further jobs from one company and medium- or low-confidence jobs sink in the list, without changing their displayed score.

## What was learned from Jobright

From the public site and its public JavaScript bundle, without calling its private APIs:

- **The location limit is hardcoded.** The client has a six-entry country enum (US, CA, GB, AU, IE, NZ), and anything else falls through to "United States". This is a product scope limit, so no setting changes it.
- **The feed is driven by a saved filter** (`/swan/filter/update/filter-v2`) holding job titles from a taxonomy, locations, `workModel`, seniority, excluded skills and excluded company categories.
- **Scores are shown as** `displayScore` with `rankDesc`, split into `jobTitleMatchSeverity`, `industryScores`, `skillMatchingScores` and years of experience. Scout's breakdown mirrors these signals.
- **Not visible from outside:** the ranking model, its weights and its data pipeline run on the server. Scout's are its own design, not a copy.

## Limits

- **Coverage is much smaller than Jobright's**, which advertises about 8 million jobs.
- **Hybrid is undercounted for Nigerian jobs.** Board listings give short excerpts that rarely state the setup. "Lagos" without "Hybrid" means unstated, so it may be either.
- **Nigerian places are a word list** in `scout/eligibility.py`: cities, states and Lagos areas. It decides whether a remote job is tied to Nigeria, and which postings get their text read for remote or hybrid wording. A place it doesn't know is treated as abroad. Names shared with places abroad only count with a qualifier ("Benin City", "Delta State"), and Lagos areas such as Maryland and Marina only count alongside "Lagos".
- **Nigeria scores are rougher** because they're based on those excerpts rather than full postings.
- **Eligibility is rule-based and will sometimes be wrong.** Boards' labels can't always be trusted, and the rules only catch restrictions phrased in ways they recognise. Read the posting before applying. `scout audit` shows every decision.
- **BambooHR's setup field is inferred.** Its on-site, remote and hybrid codes were worked out from live boards, as they aren't documented.
- **HTML sources can break** if Jobberman or MyJobMag change their markup. `ingest` reports a count per source, so a drop to 0 is visible.

## Source etiquette

- Identifies itself honestly and fetches slowly. Nigerian boards use listing pages that robots.txt permits, and only excerpts are kept.
- Remote OK's API terms require naming and linking back to it: each job's page does this. Don't remove that if you publish this.
- **HotNigerianJobs** comes in through its public RSS feed only, keeping titles, links and the feed's excerpts. Its robots.txt allows search use (`search=yes`) and forbids AI training (`ai-train=no`), and blocks named AI crawlers such as ClaudeBot. Scout is your own program, runs under its own name and trains nothing. Its terms prohibit reproducing its content, so don't publish what Scout stores from it. To turn it off, remove it from `SOURCES` in `scout/sources.py`.
- **Indeed is not included.** It answers 403 to an honest client, and Scout doesn't disguise itself to get past that.
- This is built for personal use. Check each source's terms before making it a public product.
