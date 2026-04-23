# Setup — one-time manual steps

The agent cannot push to GitHub or modify your git config for you. This
document lists every step **you** must run once to activate the public track
record.

## 1. Create the public GitHub repo

```bash
cd /Users/tars/trading-lab/paper_trading/track_record
gh repo create paper-trading-track-record \
    --public \
    --source=. \
    --remote=origin \
    --description="Cryptographically timestamped track record of systematic paper trades" \
    --push
```

If you prefer to do it in two steps:

```bash
gh repo create romandrianov-pixel/paper-trading-track-record --public
git remote add origin https://github.com/romandrianov-pixel/paper-trading-track-record.git
git push -u origin main
```

## 2. (Optional) GPG-signed commits

Signed commits add another layer of provenance. If you have a GPG key
configured:

```bash
git config --local user.signingkey <YOUR_KEY_ID>
git config --local commit.gpgsign true
```

`commit_daily.py` will pass `-S` automatically if `commit.gpgsign=true` is
set in the local config.

## 3. Enable the daily commit hook

Add to the runner's environment (edit the launchd plist or your shell
profile):

```bash
export PAPER_TRACK_RECORD=1
export PAPER_TRACK_RECORD_PUSH=1   # set to 0 to commit-only, skip push
```

Or flip the flag in `config.yaml`:

```yaml
track_record:
  enabled: true
  push: true        # require PAPER_TRACK_RECORD_PUSH=1 or this true
```

## 4. Install the weekly OpenTimestamps launchd plist

```bash
cp /Users/tars/trading-lab/paper_trading/com.roma.paper_ots.plist \
   ~/Library/LaunchAgents/com.roma.paper_ots.plist
launchctl load  ~/Library/LaunchAgents/com.roma.paper_ots.plist
launchctl start com.roma.paper_ots
```

The job fires Sunday 22:00 UK and runs `track_record/ots_weekly.py`.

## 5. Verify the install

```bash
cd /Users/tars/trading-lab/paper_trading/track_record
python3 verify.py --date 2026-04-23    # first-day bootstrap
ls -la .ots/                           # will be empty until first Sunday
git log --oneline                      # should show the bootstrap commit
```

## Troubleshooting

- `ots` not found: it's installed in `../.venv-ots/bin/ots`. The weekly
  script uses that path directly; no shell changes required.
- `gh: command not found`: `brew install gh && gh auth login`.
- Push denied: confirm `gh auth status` shows the `romandrianov-pixel`
  account and the remote URL matches.
