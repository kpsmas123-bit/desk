# Desk — Setup Guide

These steps get your secret keys onto your computer and over to GitHub.
You don't need to know how to code — just follow them in order.

## 1. Make your own .env file

Find the file called **`.env.example`** in this project folder.
Make a copy of it and name the copy **`.env`** (with nothing before the dot).

- On a Mac, you can open the Terminal app, type the line below, and press Enter:

  ```
  cp .env.example .env
  ```

## 2. Paste your real keys into .env

Open the new **`.env`** file in any text editor.
You'll see lines like `SUPABASE_URL=` with nothing after the `=`.
Paste your real value right after each `=` sign, with no spaces and no quotes.

For example, it should end up looking like:

```
SUPABASE_URL=https://abcd1234.supabase.co
```

Save the file when you're done.

## 3. Install the GitHub CLI and sign in

The GitHub CLI is a small tool that lets your computer talk to GitHub.

1. Install it by following the instructions at: https://cli.github.com/
2. Once it's installed, open Terminal and run:

   ```
   gh auth login
   ```

3. Answer the prompts (choose **GitHub.com**, then sign in through your web browser).

## 4. Copy your keys to GitHub

Now send the keys from your `.env` file up to GitHub so the scheduled job can use them.
In Terminal, from inside this project folder, run:

```
gh secret set --env-file .env
```

That's it — your keys are now stored securely on GitHub.

---

> **Note:** Your `.env` file stays on your own computer. It is listed in `.gitignore`,
> so it is **never** uploaded to GitHub when you share your code. Only the empty
> template (`.env.example`) gets shared.

---

## 5. Create the database tables (run the schema)

The database starts out empty. This step creates all the tables your project
needs. You only have to do it **once**.

1. Open your web browser and go to **https://supabase.com/dashboard**. Sign in if
   it asks you to, then click on the **desk** project to open it.

2. On the left-hand sidebar, click **"SQL Editor"**. Then click the
   **"New query"** button (usually near the top).

3. On your computer, open the file **`supabase/migrations/0001_init.sql`**
   (it's inside this project folder). Select **all** of the text in that file
   (click inside it and press **Cmd+A** on a Mac to select everything), copy it
   (**Cmd+C**), then click into the big empty editor box in your browser and
   paste it in (**Cmd+V**). Finally, click the **"Run"** button.

   You should see a small "Success" message. (You can safely ignore any
   "no rows returned" note — that just means the command finished.)

4. Now, back on the left sidebar, click **"Table Editor"**. You should see
   **8 tables** listed:

   - `pages`
   - `subpages`
   - `sources`
   - `stories`
   - `threads`
   - `story_threads`
   - `notes`
   - `saved`

   If all 8 are there, your database is set up correctly. 🎉

## 6. Add the enrichment columns (run the second migration)

Later phases sort and classify stories, which needs a few extra columns. This is
a small, **safe** update — it only *adds* columns and changes nothing that's
already there. You only have to do it **once**, and you should do it **before**
running `ingest/enrich.py` for the first time.

Run it exactly the same way you ran the first one:

1. Go to **https://supabase.com/dashboard**, open the **desk** project, and on the
   left sidebar click **"SQL Editor"**, then **"New query"**.

2. Open the file **`supabase/migrations/0002_enrich.sql`** in this project folder,
   select all of it (**Cmd+A**), copy it (**Cmd+C**), paste it into the editor box
   (**Cmd+V**), and click **"Run"**.

   You should see a "Success" message. (A "no rows returned" note is fine — it just
   means the command finished.)

That's it — your `stories` table now has the `relevance_score`, `topics`, and
`enriched_at` columns the enrichment script fills in.
