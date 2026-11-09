# Setting up WebWander

Everything here runs on free tiers: GitHub Pages and GitHub Actions (public repo) plus
Firebase's free Spark plan. Setup takes about 15 minutes, all in web consoles.

## 1. Create the Firebase project

1. Go to <https://console.firebase.google.com> and click **Create a project** (call it `webwander`).
   Google Analytics is optional; you can turn it off.
2. On the project overview, click the **</>** (Web) icon to add a web app. Name it `webwander`,
   skip Firebase Hosting, and click **Register app**.
3. Copy the `firebaseConfig` object it shows into [`site/js/firebase-config.js`](site/js/firebase-config.js),
   replacing the `REPLACE_ME` values, and commit. (These values are meant to be public. The
   Firestore rules are what protect the data.)

## 2. Turn on Google sign-in

1. **Build > Authentication > Get started**, then pick **Google** under sign-in providers and enable it.
2. **Authentication > Settings > Authorized domains > Add domain** and add `mendus59.github.io`.

## 3. Create the database

1. **Build > Firestore Database > Create database**. Pick a location near you and start in
   **production mode**.
2. Open the **Rules** tab, replace everything with the contents of
   [`firebase/firestore.rules`](firebase/firestore.rules), and click **Publish**.
3. Create the indexes. The easiest way is the Firebase CLI from this repo's folder:
   ```sh
   npm install -g firebase-tools
   firebase login
   firebase use --add          # pick your project
   firebase deploy --only firestore
   ```
   This deploys both the rules and [`firebase/firestore.indexes.json`](firebase/firestore.indexes.json).
   If you'd rather not use the CLI, the site still works: the first time a query needs an index,
   the browser console shows a link that creates it in one click.

## 4. Give the crawler access

1. **Project settings (gear) > Service accounts > Generate new private key**. A JSON file downloads.
2. In GitHub: **repo Settings > Secrets and variables > Actions > New repository secret**.
   Name it `FIREBASE_SERVICE_ACCOUNT` and paste the **entire** JSON file as the value.
3. Delete the downloaded file. Never commit it.

## 5. Turn on GitHub Pages

**repo Settings > Pages > Build and deployment > Source: GitHub Actions.** The
"Deploy site to GitHub Pages" workflow publishes `site/` to
<https://mendus59.github.io/webwander/> on every push to `main`.

## 6. Fill the database

**Actions > Seed database > Run workflow.** It scores and tags every URL in
[`crawler/seeds.txt`](crawler/seeds.txt) (about 150 hand-picked sites) and approves them,
so there's something to wander on day one. Uncheck "approve" if you'd rather review them
yourself in the admin page first.

## 7. Make yourself an admin

1. Open the site, click **Sign in**, then go to `/admin.html`.
2. It shows your user ID and the exact document to create. In **Firestore > Data**,
   click **Start collection**, name it `admins`, use your user ID as the document ID, and
   add any field (for example `email`). Reload the admin page.

## 8. Done

The **Daily crawl** workflow runs every day at 13:17 UTC and adds at least 10 new sites to
your review queue. You can also run it by hand from the Actions tab (or the "Run crawler"
button on the admin page).

### Good to know

- **Free-tier limits.** Firestore's free tier allows 50k reads and 20k writes per day, far
  more than a personal site needs. Each crawl reads the site list once.
- **Scheduled workflows** on GitHub pause after 60 days without repo activity. The crawler
  commits its bookkeeping to `data/` after every run, which keeps the repo active.
- **Some sites refuse to be embedded** (X-Frame-Options). The crawler detects this and the
  site opens those in a new tab instead of the frame.
