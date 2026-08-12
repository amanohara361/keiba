@echo off
rem ---------------------------------------------------------------
rem  DISABLED 2026-08-12. Do not commit or push data/ to GitHub.
rem
rem  Cowork and the cloud pipeline (Claude Code Remote Routines +
rem  GitHub Actions) now both write data/bets/YYYY-MM-DD.json
rem  independently. When this script pushed Cowork's local copy on
rem  top of the cloud copy, git pull --rebase hit a real conflict on
rem  the same file and could leave the repo on this PC stuck mid
rem  rebase until someone ran git rebase --abort by hand. The NAR
rem  routine also merges its races into the existing file; a Cowork
rem  push that skipped that merge could silently drop them.
rem
rem  The fix is to keep the two systems fully separate instead of
rem  reconciling them: Cowork stays local only (buy-list txt, Excel,
rem  Drive, Gmail - easier to skim than digging through GitHub commit
rem  history), the cloud pipeline stays on GitHub only. Neither reads
rem  or writes the other's files, so there is nothing left to
rem  conflict. See the repo's decision log (docs folder, Japanese
rem  filename) for the full account.
rem
rem  This script is kept as a stub, not deleted, so a Task Scheduler
rem  entry still pointing at it fails loudly in the log instead of
rem  silently doing nothing. Remove the Sat/Sun 08:00 and 12:00
rem  Task Scheduler entries on this PC; this file no longer needs to
rem  run at all.
rem
rem  ASCII only and CRLF line endings, per CLAUDE.md.
rem ---------------------------------------------------------------

setlocal
set LOG=E:\Claude\github_push_log.txt

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd_HH:mm"') do set NOW=%%i

echo. >> "%LOG%"
echo ===== %NOW% ===== >> "%LOG%"
echo DISABLED: this script no longer commits or pushes. See github_push.bat and the repo decision log. >> "%LOG%"
echo DISABLED: this script no longer commits or pushes. Remove its Task Scheduler entry.

endlocal
exit /b 0
