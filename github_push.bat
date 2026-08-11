@echo off
rem ---------------------------------------------------------------
rem  Commit and push data/ to GitHub.
rem
rem  The Cowork sandbox can create and update files on the mounted
rem  folder but cannot unlink them, so git cannot run there
rem  (git needs to create and remove .git/index.lock on every commit).
rem  The morning task therefore only writes the JSON, and this script
rem  performs the commit and push on the Windows side, where the
rem  credential manager is already authenticated.
rem
rem  Register in Task Scheduler for Sat and Sun, 08:00 and 12:00.
rem  Running it when there is nothing to commit is harmless.
rem
rem  ASCII only and CRLF line endings, per CLAUDE.md.
rem  No goto: 2026-08-02 an LF-saved batch failed to resolve labels
rem  and the window closed instantly with no message.
rem ---------------------------------------------------------------

setlocal
set REPO=E:\Claude\keiba-repo
set LOG=E:\Claude\github_push_log.txt

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd_HH:mm"') do set NOW=%%i
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set TODAY=%%i

echo. >> "%LOG%"
echo ===== %NOW% ===== >> "%LOG%"

if not exist "%REPO%\.git" (
    echo ERROR: %REPO% is not a git repository >> "%LOG%"
    echo ERROR: %REPO% is not a git repository
    endlocal
    exit /b 1
)

cd /d "%REPO%"

rem A stale lock left by a crashed process blocks everything. Clear it.
if exist ".git\index.lock" (
    echo Removing stale .git\index.lock >> "%LOG%"
    del ".git\index.lock"
)

git add data/ >> "%LOG%" 2>&1

git diff --staged --quiet
if not errorlevel 1 (
    echo Nothing to commit >> "%LOG%"
    echo Nothing to commit
    endlocal
    exit /b 0
)

git commit -m "Add bets: %TODAY%" >> "%LOG%" 2>&1
if errorlevel 1 (
    echo ERROR: commit failed >> "%LOG%"
    echo ERROR: commit failed - see %LOG%
    endlocal
    exit /b 1
)

git pull --rebase --autostash origin main >> "%LOG%" 2>&1
git push origin HEAD:main >> "%LOG%" 2>&1
if errorlevel 1 (
    echo ERROR: push failed >> "%LOG%"
    echo ERROR: push failed - see %LOG%
    endlocal
    exit /b 1
)

rem Confirm the push actually landed, per CLAUDE.md.
git log --oneline -1 origin/main >> "%LOG%" 2>&1
echo Pushed successfully >> "%LOG%"
echo Pushed successfully
git log --oneline -1 origin/main

endlocal
exit /b 0
