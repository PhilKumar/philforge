#!/bin/bash
# Refresh everything that publishes the five-year book, in the order it must
# happen. Run weekly.
#
# The order is the whole point. A commit on 2026-09-02 rebuilt the data and the
# landing but skipped step 3, so docs/assets/backtest-tearsheet-5yr.html — the
# document the Assets page actually serves — kept the August figures. Playwright
# caught it, the deploy was skipped, and Gap Carry did not go live until the
# render was run by hand.
#
# It stops at the first failure and it never commits or pushes: it leaves the
# working tree changed and says so, because publishing a track record is Phil's
# call, not a cron job's.
set -euo pipefail

cd "$(dirname "$0")"
REPO="$(cd ../.. && pwd)"

step() { printf '\n=== %s\n' "$1"; }

step "1/5  Is the machinery still faithful?"
# Reproduces the historical book and requires it to match what was published.
# If this is not MATCHES, nothing below should run: the numbers cannot be
# trusted, and a wrong figure on the page is worse than an old one.
if ! python3 rebuild_data.py --check | tee /tmp/pf_refresh_check.log | tail -3; then
  echo "ABORT: rebuild_data.py --check failed"; exit 1
fi
if ! grep -q "^MATCHES" /tmp/pf_refresh_check.log; then
  echo "ABORT: the check did not say MATCHES — do not publish. See /tmp/pf_refresh_check.log"
  exit 1
fi

step "2/5  Rebuild the book"
# report_data.json moved, 2026-09-15, from the legacy splice book to a ladder/
# configuration report built off the saved strategies (#48 CE, #50 PE) in
# philforge.db: 34 separate replay runs through philforge_strategy_on_dhan.py
# stitched together by a rebase script that only ever lived in a session
# scratchpad. rebuild_data.py is the OLD builder and refuses to touch a report
# in the new shape (see _guard_current_report) rather than overwrite it with
# different numbers. Reproducing that pipeline as a real weekly job is real
# engineering, not something this script attempts on its own — when
# report_data.json is in the new shape, skip the rebuild and fall through to
# step 5, which still catches the surfaces drifting apart from each other even
# though nothing here re-derives the book from source data.
if python3 -c "
import json, sys
d = json.load(open('report_data.json'))
sys.exit(0 if (d.get('deployed') or d.get('live_config')) else 1)
"; then
  SKIP_REBUILD=1
  echo "SKIPPED — report_data.json is the newer ladder/configuration report."
  echo "Rebuilding it is a manual/Claude-assisted job (saved-strategy replays + rebase),"
  echo "not something weekly_refresh.sh does. Falling through to step 5 to at least"
  echo "confirm the checked-in surfaces still agree with each other."
else
  SKIP_REBUILD=0
  python3 rebuild_data.py --write

  step "3/5  Re-render the served document   <- the step that was missed"
  python3 build_report.py
  echo "wrote docs/assets/backtest-tearsheet-5yr.html"

  step "4/5  Regenerate the landing from the book"
  python3 update_landing.py --write
fi

step "5/5  Prove every published surface agrees"
cd "$REPO"
python3 -m pytest tests/test_published_figures_match_data.py -q
python3 tools/tearsheet/update_landing.py --check

step "What changed"
# Both generated files carry a "generated: <today>" stamp, so a run on a day when
# the book did not move still rewrites two files with nothing but a new date in
# them. Left alone that would hand Phil a date-only commit every single week, and
# since a push auto-deploys PhilForge, a weekly restart of a live trading app for
# no reason at all. So: if a generated file differs ONLY by its date stamp, put
# it back and say nothing changed. Any real difference is left exactly as it is.
DATED="tools/tearsheet/report_data.json docs/assets/backtest-tearsheet-5yr.html"
for f in $DATED; do
  if ! git -C "$REPO" diff --quiet -- "$f"; then
    # Every grep here can legitimately match nothing, and under `set -o
    # pipefail` a grep that matches nothing fails the whole pipeline and, with
    # `set -e`, silently kills the script -- which is exactly what it did the
    # first time. Each grep is guarded so "no matches" means zero, not death.
    substantive=$(git -C "$REPO" diff -U0 -- "$f" \
      | { grep -E '^[+-]' || true; } \
      | { grep -Ev '^(\+\+\+|---)' || true; } \
      | { grep -Ev 'generated' || true; } \
      | wc -l | tr -d ' ')
    if [ "$substantive" = "0" ]; then
      git -C "$REPO" checkout -- "$f"
      echo "  $f — date stamp only, reverted"
    fi
  fi
done

CHANGED=$(git -C "$REPO" status --porcelain -- \
  tools/tearsheet/report_data.json \
  docs/assets/backtest-tearsheet-5yr.html \
  static/landing/forge.html \
  static/landing/dojima.js)

if [ -z "$CHANGED" ]; then
  echo
  if [ "$SKIP_REBUILD" = "1" ]; then
    echo "NO CHANGE — the rebuild was skipped (new-format report), but step 5 confirms"
    echo "report_data.json, the served document and the landing still agree with each other."
    echo "This does NOT confirm the book itself is still current — see step 2 above."
  else
    echo "NO CHANGE — every published surface already matches the book. Nothing to commit."
  fi
  exit 0
fi

echo "$CHANGED"
echo
if [ "$SKIP_REBUILD" = "1" ]; then
  echo "Surfaces disagree, but the rebuild step was skipped (new-format report) so nothing"
  echo "was regenerated to fix it. This needs the manual rebuild process, not this script."
  exit 1
fi
echo "THE BOOK MOVED. Nothing has been committed or pushed."
echo "Review, then commit exactly these paths:"
echo "  git commit -- tools/tearsheet/report_data.json docs/assets/backtest-tearsheet-5yr.html static/landing/forge.html static/landing/dojima.js"
