@echo off
REM Push RevenueBringer website to GitHub
REM
REM BEFORE RUNNING THIS:
REM 1. Create a repository on github.com
REM 2. Replace YOUR_USERNAME and YOUR_REPO_NAME below with your actual values
REM 3. Save this file and run it

setlocal enabledelayexpansion

cd c:\Users\micha\Ziel\website

REM Configure git (one-time)
git config --global user.name "Your Name"
git config --global user.email "your@email.com"

REM Initialize local repository
git init

REM Add all files
git add .

REM Create initial commit
git commit -m "Initial commit: RevenueBringer website"

REM Rename branch to main
git branch -M main

REM Add remote (replace YOUR_USERNAME and YOUR_REPO_NAME)
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO_NAME.git

REM Push to GitHub
git push -u origin main

echo.
echo Done! Your website is now on GitHub.
pause
