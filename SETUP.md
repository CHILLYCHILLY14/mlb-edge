# MLB Edge — setup and updates, start to finish

Written for someone who has never used GitHub. No coding. About 20 minutes the
first time, then it runs itself.

**What you end up with:** a web address of your own that rebuilds the whole model
eight times a day and shows today's slate, and that address dropped into your Wix
page so it appears as part of your site.

**What you need:** the `mlb-edge.zip` file, an email address, and a free GitHub
account. No credit card. Nothing to install.

---

## Part 1 — Install it (you do this once)

### Step 1. Unzip the file

Double-click `mlb-edge.zip`. You get a folder called **mlb-edge**. Open it. You
should see roughly this:

```
.github/        data/       docs/       pipeline/
tests/          tools/      README.md   SETUP.md      requirements.txt
```

Leave that window open — you will drag from it in Step 4.

> Some computers hide folders whose name starts with a dot, so you may not see
> `.github`. That is fine and expected. Step 5 handles it.

### Step 2. Make a GitHub account

Go to **github.com** and click **Sign up**. Free plan. Pick any username — it
becomes part of your web address, so `chillychilly14` gives you
`chillychilly14.github.io/mlb-edge`.

### Step 3. Create the repository

A repository ("repo") is just a folder that lives on GitHub.

1. Top-right, click the **+** → **New repository**
2. **Repository name:** `mlb-edge` (lower case, with the hyphen)
3. Choose **Public**
   *This matters.* Free GitHub Pages and unlimited free Actions minutes only
   apply to public repos. Nothing private is in here — no passwords, no keys.
4. Leave every checkbox **unticked** (no README, no .gitignore, no license)
5. Click **Create repository**

You land on a mostly empty page with setup instructions. Ignore all of it.

### Step 4. Upload the files

1. On that page find the link **uploading an existing file** and click it.
   (If you cannot see it: **Add file** → **Upload files**.)
2. Go back to your unzipped **mlb-edge** folder.
3. Select **everything inside it** — not the folder itself, its contents.
   Windows: click one item, then Ctrl+A. Mac: click one item, then Cmd+A.
4. Drag the selection into the browser window.
5. Wait until every file finishes. It is a couple of hundred small files.
6. Scroll down, click **Commit changes**.

You should now see `docs`, `pipeline`, `tests`, `tools`, `data`, `README.md` and
`requirements.txt` listed in your repo.

### Step 5. Add the automation file

This is the file that makes everything run by itself. It lives in a hidden
folder, so we create it directly on GitHub instead of relying on the drag.

1. Click the **Actions** tab at the top of your repo.
2. **If you see a workflow called "Build MLB slate"** — the upload worked, the
   hidden folder came through. Skip to Step 6.
3. **If you see a "Get started with GitHub Actions" page** — click
   **set up a workflow yourself**.
4. An editor opens with some starter text. Select all of it and delete it.
5. Open `.github/workflows/build.yml` from the unzipped folder in a plain text
   editor (Notepad on Windows, TextEdit on Mac) and copy everything.
   *Can't find the `.github` folder?*
   Windows: File Explorer → **View** → tick **Hidden items**.
   Mac: in Finder press **Cmd + Shift + .** (period) to show hidden files.
6. Paste it into the GitHub editor.
7. At the top, change the filename box to `build.yml`.
8. Click **Commit changes** → **Commit changes** again.

### Step 6. Turn the website on

1. Click **Settings** (top of the repo, far right).
2. Left sidebar → **Pages**.
3. Under **Build and deployment**, set **Source** to **GitHub Actions**.

There is no save button. It applies immediately.

### Step 7. Run it for the first time

1. Click the **Actions** tab.
2. If it asks, click **I understand my workflows, go ahead and enable them**.
3. Left sidebar → **Build MLB slate**.
4. Right side → **Run workflow** → green **Run workflow** button.
5. Refresh after a few seconds. A yellow dot means running, a green tick means
   done. It takes about 4–8 minutes the first time.

### Step 8. Open your dashboard

**Settings** → **Pages**. At the top is your address:

```
https://YOUR-USERNAME.github.io/mlb-edge/
```

Click it. You should see today's games. Bookmark it.

> If it says "No feed for this date yet", the build has not finished. Wait for
> the green tick in the Actions tab and refresh.

---

## Part 2 — Put it on your Wix site

1. Open your site in the **Wix Editor**.
2. **Add** (the **+**) → **Embed Code** → **Embed a Site**.
3. A box appears. Click **Enter Website Address** and paste your address.
4. Drag the box wide — full width of the page.
5. Drag it tall. **1400 px** is a good starting height on mobile; **2200 px** if
   most of your visitors are on a computer. The dashboard scrolls inside the box
   either way, so too short is annoying but not broken.
6. **Publish**.

### Address options

Add these to the end of your address to change how it opens:

| Add this | What it does |
|---|---|
| `?theme=light` | forces the light color scheme — use it if your Wix page is white |
| `?theme=dark` | forces dark, whatever the visitor's phone is set to |
| `?tab=bets` | opens straight to today's card |
| `?tab=mine` | opens straight to My Ledger |

Combine them with `&`, like
`https://YOU.github.io/mlb-edge/?theme=light&tab=bets`.

---

## Part 3 — Using it

**It updates itself.** Eight times a day: early morning, late morning,
afternoon, when lineups post, before first pitch, mid-evening, late, and
overnight. You do not have to do anything.

**To force a refresh right now:** Actions tab → **Build MLB slate** →
**Run workflow**. Give it five minutes.

**Reading a game:** each card shows the projected score, both win percentages
after the market blend, the total against the book's number, and the single best
number in the game with its tier. Tap **Full breakdown** for the starters, the
run distribution and every market.

**Tracking a bet:** tap **+ Ledger** on any LEAN, GOOD or BEST BET. It saves at
that price with the suggested stake. Open **My Ledger** to change the stake, and
it settles itself once the game finishes.

**Back up your ledger.** It lives in your browser, so clearing your browsing data
erases it. In My Ledger tap **Export JSON** every so often and keep the file.
**Import** puts it back.

---

## Part 4 — Changing a setting

### The easy way: bankroll

**Settings** → **Secrets and variables** → **Actions** → **Variables** tab →
**New repository variable**.

| Name | Value | What it does |
|---|---|---|
| `MLB_BANKROLL` | `250` | the bankroll all staking is sized from |
| `MLB_SEASON` | `2026` | which season's stats to use |

Then run the workflow once so it picks the change up.

### Everything else: `pipeline/config.py`

1. In your repo click **pipeline**, then **config.py**.
2. Click the **pencil** icon (top right of the file).
3. Change the number you want. Every line has a comment explaining it.
4. Scroll down → **Commit changes** → **Commit changes**.
5. Actions → **Build MLB slate** → **Run workflow**.

The settings people actually change:

| Setting | Default | Raise it to… | Lower it to… |
|---|---|---|---|
| `KELLY_FRACTION` | `0.25` | bet more per edge | bet less |
| `MAX_STAKE_PCT` | `0.05` | allow bigger single bets | cap them tighter |
| `TIER_BEST` | `0.035` | make BEST BET rarer | make it more common |
| `TIER_GOOD` | `0.025` | fewer GOOD calls | more |
| `MARKET_BLEND` | `0.40` | trust the market more | trust the model more |
| `MAX_PLAYS_PER_SLATE` | `6` | more bets per day | fewer |
| `MAX_SLATE_EXPOSURE_PCT` | `0.15` | more money at risk daily | less |
| `N_SIMS` | `20000` | more precision, slower builds | faster builds |

> Careful with `KELLY_FRACTION` and `MAX_STAKE_PCT`. Those two are what the
> audit in your old workbook said had done the damage — a 55% win rate with a
> −24% return is a staking problem, not a picking problem.

---

## Part 5 — Installing an update

When you get a new `mlb-edge.zip`:

1. **First, back up your ledger.** Open the dashboard → **My Ledger** →
   **Export JSON** → save the file.
2. Unzip the new version.
3. **Delete `data/shadow.json` from the new unzipped folder.** That file in your
   repo holds every graded call the model has made; the copy in the zip is empty
   and would wipe your history.
4. In your repo: **Add file** → **Upload files**.
5. Select everything inside the new unzipped folder and drag it in. Same-named
   files are replaced; your history is untouched.
6. **Commit changes**.
7. If the update changed `.github/workflows/build.yml`, redo Step 5 from Part 1
   to paste the new version.
8. Actions → **Build MLB slate** → **Run workflow**.

Nothing about your dashboard address changes. Anyone with the Wix page keeps
seeing it, updated.

---

## Part 6 — When something looks wrong

| What you see | What it means | What to do |
|---|---|---|
| Red ✗ on the workflow run | the build failed | Click the run → click the red step → read the last few red lines. Most often a data source was briefly down; just run it again. |
| "No feed for this date yet" | no build has finished for that date | Actions → Run workflow, wait for the green tick, refresh. |
| Page loads but every game says **no price** | ESPN had no odds posted yet | Normal early in the morning. Odds appear through the day; the next build picks them up. |
| **404** at your github.io address | Pages is not switched on, or the first build has not finished | Settings → Pages → Source = **GitHub Actions**, then run the workflow. |
| Wix shows an empty white box | the address is wrong or missing `https://` | Re-copy it from Settings → Pages. It must start with `https://`. |
| The dashboard is there but tiny/cut off | the Wix embed box is too short | In the Wix editor drag the box taller. It does not resize itself. |
| My Ledger is empty | browser data was cleared, or you are on a different device | It is stored per browser. Import the JSON you exported. |
| Same numbers all day | you are looking at a cached page | Pull down to refresh, or add `?x=1` to the address. |
| Red **divergence flag** banner | the model disagrees with the market on an unusual share of the slate | Usually a data problem, not free money. The model already capped its own best bets. Treat that day's edges with suspicion. |
| Lots of games say **lineups projected** | batting orders are not posted yet | Normal until a few hours before first pitch. Best bets need confirmed starters, so more appear as the day goes on. |

**Reading a failed run:** Actions → click the run → click **build** → the step
with the red ✗ opens to show the log. The useful line is usually the last one
that is not indented.

---

## Part 7 — What the words mean

| Term | Plain English |
|---|---|
| **Edge** | how much better the model thinks a price is than the book does, as a percentage of what you stake. `+3.2%` means the model expects to make 3.2 cents per dollar. |
| **Fair** | the price the model thinks the bet *should* be. If the book is longer than fair, that is where the edge comes from. |
| **Model %** | the model's chance of the bet winning, after being pulled toward the market. |
| **Market %** | the book's chance, with its own margin taken out. |
| **No-vig** | the book's price with its built-in profit removed, so it can be compared with the model fairly. |
| **Tier** | how much the model likes it: BEST BET, GOOD, LEAN, PASS. |
| **Kelly** | the math for how much to bet given an edge. This uses a quarter of it, which is deliberately conservative. |
| **Run line** | the baseball version of a spread, almost always 1.5 runs. |
| **F5 / first five** | just the first five innings. No bullpen, so often the cleanest read. |
| **Total** | the combined runs both teams score. Over or under the book's number. |
| **CLV** | closing line value — whether the price you took was better than where the market ended up. Beating the close is the best short-run evidence an edge was real. |
| **Shadow book** | every call the model makes, graded, including the ones it told you to skip. That is how you know if the tiers are set right. |
| **Park factor** | how much a stadium adds or subtracts. `112/111` means 12% more runs and 11% more home runs than average. |
| **Power rating / True W%** | what the model thinks a roster would do against average opposition, ignoring luck and schedule. |

---

## Still stuck?

Open the **Actions** tab, click the most recent run, and copy the last twenty
lines of whichever step is red. That is almost always enough to tell what went
wrong.
