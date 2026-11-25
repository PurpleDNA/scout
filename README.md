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

Each ingest also deletes jobs Scout has held for 60 days once they have closed or gone stale. Jobs you reacted to are kept: applied ones for your records, liked and "not for me" ones because the ranking learns from them. A job its board still lists with no posting date is kept as well, since deleting it would bring it straight back as new.

**Trainee and internship programmes** have their own page at http://127.0.0.1:8787/programmes (the "Programmes" button). They come from jobs.revicemycv.com, whose strength is Nigerian graduate trainee schemes, and are not ranked, scored or used as feedback. Round-up posts ("multiple positions") are dropped unless they name a tech role. Programmes are sorted by deadline, read from the description, and drop off once it passes; undated ones follow, newest first. Mark them applied or hide them. They are deleted 60 days after Scout first saw them, unless you applied. `scout ingest --only programmes` refreshes just these.

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
sources.py      20 public feeds, job boards and hiring systems  →  RawJob
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

## Finding companies' own careers pages (pilot)

Many Nigerian employers never post to a job board. `scout companies` lists companies from public registers, finds each one's careers page and the hiring system behind it, and reports what it found. It doesn't add jobs to the feed yet.

```bash
uv run scout companies seed               # NGX, CBN, SEC, NCC and Wikidata
uv run scout companies seed --mfbs        # also CBN's ~790 microfinance banks
uv run scout companies crawl              # about 35 minutes for ~1,100 companies; rechecks only what's over a week old
uv run scout companies report             # yield by status and industry, plus ATS slugs ready for scout/sources.py
uv run scout companies report --industry Banking
uv run scout companies list --status ats
```

| Register | What it gives |
|---|---|
| NGX company directory | Listed companies with website, sector and subsector |
| CBN supervision lists | Banks, discount houses, finance companies and mortgage banks. Names only, so the website is guessed from the name and only kept if the page names the company |
| SEC register of capital market operators | About 900 fund managers, brokers, trustees, solicitors and auditors. The website comes from the operator's own email domain. Webmail addresses are ignored |
| SEC fintech directory | Fintechs in the SEC's sandbox and categories, such as Cowrywise, Chaka and Busha |
| NCC individual licences | About 1,300 ISPs, value-added services, MVNOs and infrastructure firms. Licences that have expired are skipped. Names only, so websites are guessed. The ~10,000 class licences (cyber cafés, installers) are left out |
| Wikidata | Nigerian organisations with an official website and industry. Universities and NGOs are skipped |

To look through the results, run `uv run scout serve` and open http://127.0.0.1:8787/pilot. It lists every company whose openings a person has to go and look at: its own careers page, a hiring system Scout can't read, LinkedIn, or "email us your CV", with the address. Companies on a hiring system Scout already reads are left out, since their jobs come in through `ingest`. Filter by industry and register, star companies, add notes and mark them reviewed. Those marks are saved in `data/pilot.db`, in a table of their own that seeding and recrawling never touch. Set `SCOUT_PILOT_DB` to point it elsewhere. To refresh only some companies, run `scout companies crawl --status email linkedin --all`.

Each company ends up with one status: `ats` (Greenhouse, Lever, Workday, SuccessFactors and so on, with the slug when visible), `custom` (its own careers page), `linkedin`, `email` (the page only says to send a CV), `no_careers`, `js_only` (the site is built in JavaScript, so there's nothing to read without a browser), `blocked` or `unreachable`. Slugs for the hiring systems Scout already reads (Greenhouse, Lever, Ashby, BambooHR, Teamtailor, Workable, Zoho Recruit, Workday, SmartRecruiters, Breezy, Oracle) are printed at the end of the report, ready to paste into the lists in `scout/sources.py`.

## Emailing the companies that ask for CVs

Scout can save cold emails as drafts in an Outlook.com account, with your CV attached, and mark a company as Emailed once you send its draft. It can't send anything: it only asks Microsoft for `Mail.ReadWrite`, not `Mail.Send`.

```bash
uv run scout outreach login                    # once: open the link it prints and enter the code
uv run scout outreach preview -n 5             # read the next texts
uv run scout outreach draft -n 10              # save the next 10 as drafts
uv run scout outreach sync                     # mark what you sent. Also the "Check Outlook for sent" button on /pilot
uv run scout outreach status                   # what's left, waiting and sent, and who is held back and why
```

The texts are in `data/outreach/emails.json` (`to`, `subject`, `body` and which `cv`, or `hold` with a reason), and the account, signature and CV paths in `data/outreach/config.json`. CVs are read when drafting, so editing a PDF in place is enough. Both stay out of git. A company is drafted once. Anyone you've already emailed, applied to or marked "Not for me" is skipped. `sync` looks back a year through Sent Items, so it finds emails you sent by hand to a hiring address too. It only counts a general address such as info@ if Scout drafted to it.

It runs through Windows PowerShell with Microsoft's `Microsoft.Graph.Authentication` module, unpacked into `%LOCALAPPDATA%\Scout\modules` from the PowerShell Gallery. No Azure account is needed. Classic Outlook 2019 can't connect to Outlook.com accounts, so it isn't used.

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
- **Workday boards only return their Nigerian openings,** found through the board's own country or city filter. A board with nothing in Nigeria returns nothing.
- **SuccessFactors is read from the sitemap and each job's page.** The RSS feed sits under `/services/`, which these sites' robots.txt disallows. Older SuccessFactors portals (`career2.successfactors.eu/career?company=…`) need a browser and aren't read. Closed jobs that are still in the sitemap are skipped.
- **Oracle Recruiting boards only return Nigerian and remote openings,** since boards such as MTN's cover the whole group. A slug the crawl reports as a bare host still needs its site number (`CX_1` and so on) from the careers link before it goes into `ORACLE`.
- **SmartRecruiters is read from the public careers page,** not its Posting API: `api.smartrecruiters.com`'s robots.txt only admits LinkedIn's crawler. Only openings grouped under a Nigerian or remote location are read. Deloitte's West Africa board also recruits for its clients (ECOWAS, hospitals), so those jobs show Deloitte as the company.
- **Breezy's listing has no descriptions,** so each job's page is fetched for one.
- **Some Zoho boards leave out descriptions,** so those jobs are matched on the title alone.
- **BambooHR's setup field is inferred.** Its on-site, remote and hybrid codes were worked out from live boards, as they aren't documented.
- **HTML sources can break** if Jobberman or MyJobMag change their markup. `ingest` reports a count per source, so a drop to 0 is visible.

## Source etiquette

- Identifies itself honestly and fetches slowly. Nigerian boards use listing pages that robots.txt permits, and only excerpts are kept.
- Remote OK's API terms require naming and linking back to it: each job's page does this. Don't remove that if you publish this.
- **HotNigerianJobs** comes in through its public RSS feed only, keeping titles, links and the feed's excerpts. Its robots.txt allows search use (`search=yes`) and forbids AI training (`ai-train=no`), and blocks named AI crawlers such as ClaudeBot. Scout is your own program, runs under its own name and trains nothing. Its terms prohibit reproducing its content, so don't publish what Scout stores from it. To turn it off, remove it from `SOURCES` in `scout/sources.py`.
- **Company discovery** fetches robots.txt for every site and skips anything it disallows, and makes at most a handful of requests per company. Requests to different companies run in parallel, but each site's requests are sequential. Many company sites have broken TLS certificates. For these read-only fetches, it retries without verifying the certificate.
- **jobs.revicemycv.com** comes in through the JSON API its own site uses, which robots.txt allows. Scout reads newest first, 50 per page with 3 seconds between pages, and stops at the first page it already has, so a daily run is usually one request. Its robots.txt asks for 128 seconds between requests; Scout doesn't wait that long.
- **Indeed is not included.** It answers 403 to an honest client, and Scout doesn't disguise itself to get past that.
- This is built for personal use. Check each source's terms before making it a public product.
