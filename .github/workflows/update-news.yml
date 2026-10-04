name: Update news

on:
  schedule:
    - cron: "*/30 * * * *"   # every 30 minutes
  workflow_dispatch:          # lets you run it manually with a button

permissions:
  contents: write

jobs:
  update:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install dependencies
        run: pip install feedparser

      - name: Fetch news
        run: python fetch_news.py

      - name: Save changes
        run: |
          git config user.name "news-bot"
          git config user.email "news-bot@users.noreply.github.com"
          git add articles.json
          git diff --staged --quiet || git commit -m "Update news"
          git push
