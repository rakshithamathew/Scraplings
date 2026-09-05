# Local Scrapling Development Setup (Windows)

This checkout uses Python 3.13 and an editable installation in `.venv`. Run all commands from the repository root:

```powershell
Set-Location "C:\Users\raksh\OneDrive\Documents\GitHub\ScraplingAgent"
```

The current `origin` remote is the fork `https://github.com/rakshithamathew/Scraplings.git`. The package metadata identifies the upstream project as `https://github.com/D4Vinci/Scrapling`.

## 1. Activate the environment

PowerShell may block local activation scripts under its default execution policy. Permit scripts only for the current PowerShell process, then activate the environment:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
.\.venv\Scripts\Activate.ps1
```

Confirm that the virtual environment is active:

```powershell
python -c "import sys; print(sys.executable); print(sys.prefix)"
Get-Command python
Get-Command scrapling
```

Both commands should resolve under this repository's `.venv\Scripts` directory.

## 2. Install or reinstall dependencies

Create the environment if it does not exist:

```powershell
python -m venv .venv
```

After activation, install the cloned repository in editable mode with all optional functionality:

```powershell
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e ".[all]"
python -m pip install -r .\tests\requirements.txt
```

Running the editable install command again updates or restores missing dependencies without replacing the checkout with a separate PyPI copy:

```powershell
python -m pip install --upgrade -e ".[all]"
python -m pip install --upgrade -r .\tests\requirements.txt
python -m pip check
```

Verify editable provenance:

```powershell
python -m pip show scrapling
python -c "import scrapling; print(scrapling.__file__)"
```

`pip show` should report this repository as `Editable project location`, and `scrapling.__file__` should point into this checkout's `scrapling` directory.

## 3. Install browser dependencies

Run Scrapling's installer after installing the Python dependencies:

```powershell
scrapling install
```

Force a reinstall or repair when required:

```powershell
scrapling install --force
```

Confirm that Chromium can launch through both browser integrations:

```powershell
python -c "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); b=p.chromium.launch(headless=True); print(b.version); b.close(); p.stop()"
python -c "from patchright.sync_api import sync_playwright; p=sync_playwright().start(); b=p.chromium.launch(headless=True); print(b.version); b.close(); p.stop()"
```

## 4. Run a basic HTTP scraper

This performs a non-browser request and prints the first quote and author:

```powershell
python -c "from scrapling.fetchers import Fetcher; page=Fetcher.get('https://quotes.toscrape.com/'); quote=page.css('.quote')[0]; print(quote.css('.text::text').get()); print(quote.css('.author::text').get())"
```

## 5. Run the DynamicFetcher example

The example launches Chromium headlessly, waits for `.quote` to become visible, prints five quotes, and lets `DynamicFetcher.fetch()` close its one-off browser session:

```powershell
python .\examples\dynamic_test.py
```

## 6. Run the Spider example

The spider follows all ten pagination pages, collects 100 quotes, and writes `output\quotes.json`:

```powershell
python .\examples\quotes_spider.py
```

Validate the generated JSON:

```powershell
python -c "import json; from pathlib import Path; data=json.loads(Path('output/quotes.json').read_text(encoding='utf-8')); print(type(data).__name__, len(data)); print(data[0])"
```

## 7. Run tests

The concise local command documented by the repository is:

```powershell
python -m pytest tests -n auto
```

For the same safer grouping used by tox and CI, run browser tests sequentially, asyncio tests sequentially, and the remainder in parallel:

```powershell
python -m pytest tests -k "DynamicFetcher or StealthyFetcher"
python -m pytest tests -m "asyncio" -k "not (DynamicFetcher or StealthyFetcher)"
python -m pytest tests -m "not asyncio" -k "not (DynamicFetcher or StealthyFetcher)" -n auto
```

The verified Windows result for this checkout is 981 passed and 4 failed out of 985 tests. The four failures are Windows-specific test assumptions: three hard-code POSIX `/tmp/...` path strings, and one leaves a CSV reader file handle open while deleting its temporary directory. Browser tests pass 25/25. `pytest.ini` disables pytest's warnings plugin with `-p no:warnings`.

To run the complete Python-version matrix and pre-commit environment, install tox and run it from the repository root:

```powershell
python -m pip install tox
tox
```

## 8. Deactivate the environment

```powershell
deactivate
```

## 9. Common Windows troubleshooting

Check command resolution and ensure the `.venv` executables come before any global installation:

```powershell
Get-Command python -All | Select-Object Source
Get-Command scrapling -All | Select-Object Source
python -c "import sys, scrapling; print(sys.executable); print(scrapling.__file__)"
python -m pip show scrapling
```

Check the repository and remotes:

```powershell
git rev-parse --show-toplevel
git remote -v
git branch --show-current
```

If PowerShell blocks activation, use the process-only policy command shown above. Alternatively, run the virtual environment executable directly without activation:

```powershell
.\.venv\Scripts\python.exe --version
.\.venv\Scripts\python.exe .\examples\dynamic_test.py
```

Check dependencies and installed browser state:

```powershell
python -m pip check
python -m pip show playwright patchright
Test-Path .\scrapling\.scrapling_dependencies_installed
Get-ChildItem "$env:LOCALAPPDATA\ms-playwright"
python -m playwright install chromium
```

If browser navigation reports `ERR_NETWORK_ACCESS_DENIED`, or curl reports error 7 or `WinError 10013`, check firewall, proxy, VPN, antivirus, or sandbox network policy. These errors indicate blocked network access rather than a missing Chromium installation.

If `py` says that no Python installation exists while `python` works, use the activated environment's `python` command or `.\.venv\Scripts\python.exe` directly.

On Windows, deleting a temporary directory containing an open file raises `WinError 32`. Ensure every reader and writer is closed, preferably with a `with path.open(...) as file:` context manager.
