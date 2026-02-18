@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "PW_ROOT=%LOCALAPPDATA%\ms-playwright"
set "PW_CHROMIUM="
set "PW_HEADLESS_SHELL="

if not exist "%PW_ROOT%" (
    echo [error] Playwright browsers folder not found: "%PW_ROOT%"
    echo [hint] Run: python -m playwright install chromium
    exit /b 1
)

for /d %%D in ("%PW_ROOT%\chromium-*") do (
    if not defined PW_CHROMIUM set "PW_CHROMIUM=%%~nxD"
)
for /d %%D in ("%PW_ROOT%\chromium_headless_shell-*") do (
    if not defined PW_HEADLESS_SHELL set "PW_HEADLESS_SHELL=%%~nxD"
)

if not defined PW_CHROMIUM (
    echo [error] Chromium runtime not found under "%PW_ROOT%"
    echo [hint] Run: python -m playwright install chromium
    exit /b 1
)
if not defined PW_HEADLESS_SHELL (
    echo [error] Chromium headless shell not found under "%PW_ROOT%"
    echo [hint] Run: python -m playwright install chromium
    exit /b 1
)

echo Using PLAYWRIGHT_BROWSERS_PATH=%PW_ROOT%
echo Include browser: %PW_CHROMIUM%
echo Include browser: %PW_HEADLESS_SHELL%
set "PLAYWRIGHT_BROWSERS_PATH=%PW_ROOT%"

python -m nuitka --standalone ^
--show-progress ^
--plugin-enable=pyqt5 ^
--windows-disable-console ^
--playwright-include-browser=%PW_CHROMIUM% ^
--playwright-include-browser=%PW_HEADLESS_SHELL% ^
--include-data-files=src/browsers.jsonl=fake_useragent/data/browsers.jsonl ^
--include-data-files=src/downloader.ico=src/downloader.ico ^
--windows-icon-from-ico=src/downloader.ico ^
--output-dir=build ^
main.py
