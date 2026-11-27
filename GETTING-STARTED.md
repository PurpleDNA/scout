# Getting started with Scout on Windows

Scout finds jobs for you every day and ranks them by how well they fit your CV. It runs on your own laptop, and your CV never leaves it.

It has three pages:

- **Jobs:** openings from Jobberman, MyJobMag, HotNigerianJobs and many company job pages, best matches first.
- **Programmes:** graduate trainee schemes, internships and scholarships, sorted by closing date.
- **Companies:** about a thousand Nigerian employers that never post on job boards. For each one, Scout gives you its careers page or the email address it takes CVs at, so you can go to them directly.

You don't need to know anything about programming. Setting up takes about 20 minutes, plus up to half an hour while Scout fetches jobs for the first time.

## What you need

- A Windows laptop connected to the internet
- Your CV, saved as a PDF or Word file

## Step 1: Install Git

Git is the tool that downloads Scout and keeps it up to date.

1. Click **Start**, type **PowerShell**, and open **Windows PowerShell**. A blue or black window appears.
2. Copy this line, right-click inside the window to paste it, and press **Enter**:

   ```
   winget install --id Git.Git -e --source winget
   ```

3. If it asks whether to agree to terms, type **Y** and press **Enter**. If Windows asks whether to allow changes, click **Yes**.
4. When it says it has installed Git, **close the PowerShell window**.

If `winget` isn't recognised, download Git from https://git-scm.com/download/win instead. Run the installer and click **Next** on every screen.

## Step 2: Download Scout

1. Open **Windows PowerShell** again, as in step 1. It has to be a new window.
2. Paste these two lines, then press **Enter**:

   ```
   cd $HOME\Documents
   git clone https://github.com/PurpleDNA/scout.git
   ```

3. Close the window. Scout is now in your **Documents** folder, in a folder called **scout**.

## Step 3: Set up Scout

1. Open **File Explorer**, go to **Documents**, then open the **scout** folder.
2. Double-click **setup.bat**.
   - If a blue box says "Windows protected your PC", click **More info**, then **Run anyway**. Scout isn't signed by a big company, so Windows warns about it.
3. The first time, it downloads what Scout needs. This takes a few minutes.
4. Then it asks you some questions. Type your answer and press **Enter**. Where an answer is shown in [brackets], pressing **Enter** accepts it.
   - **Your CV:** drag your CV file from File Explorer into the window, then press **Enter**.
   - **Kind of work:** pick numbers from the list, such as `2` for sales, or `2,3` for sales and marketing.
   - **Job titles:** the titles you'd be happy to see on an offer letter, separated by commas. For example: `Sales Executive, Business Development Executive, Key Account Manager`. These matter most, so take your time.
   - **Moving:** whether you'd move to another city for the right on-site or hybrid job.
   - **Remote jobs abroad:** say **n** unless you really want them. They make each day's fetch much slower.
5. When it says **Saved**, press any key to close the window.

You can change your answers any time by double-clicking **setup.bat** again.

## Step 4: Open Scout

Double-click **start.bat**.

- **The first time**, Scout fetches jobs from every site. This takes 15 to 30 minutes, so leave the window open and let it run. Lines like `jobberman  276 jobs` mean it's working.
- When it's done, Scout opens in your web browser at http://127.0.0.1:8787.
- **Keep the black window open while you use Scout.** Closing it stops Scout.

## Every day after that

Double-click **start.bat**. The first time each day, Scout fetches new jobs for a few minutes, then opens. If you open it again the same day, it opens straight away.

## Using Scout

### Jobs

- The number by each job is how well it fits you, out of 100. Click a job to see why it got that score and what the posting says.
- **Open posting** takes you to the job ad, where you apply.
- Tell Scout what you think, so it learns what you like:
  - **Like:** jobs like this one move up.
  - **Not for me:** Scout asks why, then moves similar jobs down.
  - **Applied:** keeps the job in your Applied list. Later you can mark it as in progress, rejected or hired.
- **Nigeria** shows jobs in your city, plus remote jobs anywhere in Nigeria. **Global remote** shows remote jobs abroad, if you asked for them.

### Programmes

Graduate trainee schemes, internships and scholarships, with the ones closing soonest first. Mark each one **Applied**, or **Hide** it.

### Companies

This is where the hidden jobs are. These employers rarely post on job boards:

- **Careers page:** open it to see their current openings.
- **Email:** they ask for CVs by email. Send yours with a short note saying what role you're after.
- **No careers page:** visit their website, or find them on LinkedIn.

Filter by industry, star the companies you like, write notes, and mark each one **Applied**, **Emailed**, **Watching** or **Not for me** so you remember where you are. Your marks are saved on your laptop.

## Getting updates

When you're told there's a new version, close Scout, then double-click **update.bat**. Your answers and saved jobs are kept.

## If something goes wrong

- **"uv is not recognized"** or **"Scout isn't set up yet":** double-click **setup.bat** first.
- **"No profile.json yet":** run **setup.bat** and finish answering the questions.
- **The browser says it can't connect:** the black window must stay open. Double-click **start.bat** again.
- **A site shows 0 jobs, or FAILED:** that site was busy or down. Scout tries again tomorrow, and the other sites still work.
- **Jobs don't fit you well:** run **setup.bat** again and make your job titles more specific. Then use **Like** and **Not for me** on a dozen jobs.

For anything else, take a photo of the window and send it to whoever set Scout up for you.
