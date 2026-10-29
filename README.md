# GitHub Profile Analyzer

A Streamlit dashboard that analyzes any **public** GitHub profile. Enter a username and explore the profile, repository statistics, programming-language breakdown, repository insights and recent public activity. All data comes from the official GitHub REST API (no web scraping), and the app runs without a paid service, a database or a token.

## Features

- **Search** by username (a `github.com/...` link or `@name` also works). Pressing Enter submits the form. Empty or malformed names get a clear message.
- **Profile overview**: clickable avatar, name, bio, location, company, website, join date, and a link to the GitHub profile. Missing fields show "Not provided".
- **Statistics**: public repositories, followers, following, total stars, total forks and total repository size, calculated from the repositories that were actually fetched.
- **Repository explorer**: search by name, filter by language and original/fork, sort by stars, forks, name or last updated, page through results, and open any repository on GitHub.
- **Language analysis**: interactive donut and bar charts plus a table with bytes and percentage share, built from GitHub's repository languages endpoint.
- **Repository insights**: most-starred and most-forked repository, recently updated repositories, repositories without a description, repositories grouped by primary language, and original versus forked counts.
- **Recent activity**: recent public events (pushes, pull requests, issues, stars and more) from the Events API.
- **Error handling**: unknown users, rate limits, network failures, timeouts and malformed responses show friendly messages instead of crashing.
- **Caching**: API responses are cached for a few minutes, with a **Refresh data** button and a **Start new search** button.
- **Optional token** for a higher rate limit, loaded from the environment, a `.env` file or Streamlit secrets and never shown in the interface.

## Screenshots

Screenshots are not included yet. To add them:

1. Run the app (see below) and search for a profile.
2. Take a screenshot of each tab and save the images in the `assets/` folder, for example `assets/overview.png` and `assets/languages.png`.
3. Add them to this section:

   ```markdown
   ![Overview tab](assets/overview.png)
   ![Languages tab](assets/languages.png)
   ```

## Technologies used

| Purpose | Library |
| --- | --- |
| Web interface | [Streamlit](https://streamlit.io/) |
| API requests | [Requests](https://requests.readthedocs.io/) |
| Data processing | [Pandas](https://pandas.pydata.org/) |
| Interactive charts | [Plotly](https://plotly.com/python/) |
| Testing | [pytest](https://docs.pytest.org/) |

Language: Python 3.10 or newer.

## Project structure

```text
GitHub-Profile-Analyzer/
├── app.py                  # Streamlit interface and page layout
├── requirements.txt        # Python dependencies
├── README.md
├── .gitignore
├── .env.example            # Template for the optional GitHub token
├── .streamlit/
│   └── config.toml         # Theme and server settings
├── src/
│   ├── __init__.py
│   ├── github_api.py       # API requests, pagination, error handling, caching
│   ├── analytics.py        # Statistics, language aggregation, insights, sorting
│   └── utils.py            # Validation, date/number formatting, safe text helpers
├── assets/
│   └── README.md           # Where to put screenshots and other images
└── tests/
    ├── __init__.py
    ├── test_analytics.py   # Calculations and edge cases
    ├── test_github_api.py  # API client with a fake session (no internet)
    └── test_utils.py       # Dates, numbers, validation and safe text
```

## Prerequisites

- Python 3.10 or newer (check with `python --version`, or `python3 --version` on macOS/Linux)
- `pip` (included with Python)
- Git, if you want to publish the project
- An internet connection to reach `api.github.com`

## Installation

### 1. Get the code

```bash
git clone https://github.com/shrinivasnaik27/GitHub-Profile-Analyzer.git
cd GitHub-Profile-Analyzer
```

### 2. Create and activate a virtual environment

**Windows (PowerShell):**

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

If PowerShell blocks the script, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, or use Command Prompt: `.venv\Scripts\activate.bat`.

**macOS and Linux:**

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install the dependencies

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## Running the app

```bash
streamlit run app.py
```

Streamlit prints a local address (normally http://localhost:8501) and opens it in your browser.

### Example usage

1. Type `octocat` in the sidebar and press Enter (or click **Analyze profile**).
2. Use the tabs: **Overview**, **Repositories**, **Languages**, **Insights** and **Activity**.
3. In **Repositories**, search for part of a name, pick a language, sort by stars, and click **Open on GitHub** on any row.
4. Use **Refresh data** to ignore the cache, or **Start new search** to begin again.

## GitHub API endpoints and rate limits

| Endpoint | Used for |
| --- | --- |
| `GET /users/{username}` | Profile information |
| `GET /users/{username}/repos` | Public repositories (100 per page, up to 5 pages) |
| `GET /repos/{owner}/{repo}/languages` | Language bytes, one request per analyzed repository |
| `GET /users/{username}/events/public` | Recent public events (one page of 100) |
| `GET /rate_limit` | Remaining requests shown in the sidebar (does not count toward the limit) |

Without a token, GitHub allows **60 requests per hour** per IP address. One analysis uses about 3 requests plus one per repository analyzed for languages (20 by default, adjustable in the sidebar). Cached results are reused, so searching the same user again within the cache time costs nothing. If the limit is reached the app tells you when it resets and shows whatever data it already has.

## Optional: use a GitHub token (higher rate limit)

A token raises the limit to 5,000 requests per hour. The app works fine without one. Never paste a token into the source code.

1. Open <https://github.com/settings/tokens> and create a token. A fine-grained token with no extra permissions is enough, because only public data is read.
2. Provide it using **one** of these options:

   - **`.env` file** (local use): copy the template and edit the new file.

     ```bash
     cp .env.example .env        # Windows: copy .env.example .env
     ```

     Then set `GITHUB_TOKEN=your_token_here` in `.env`.

   - **Environment variable**: `export GITHUB_TOKEN=your_token_here` (macOS/Linux) or `$env:GITHUB_TOKEN="your_token_here"` (PowerShell).

   - **Streamlit secrets**: create `.streamlit/secrets.toml` containing `GITHUB_TOKEN = "your_token_here"`.

3. Restart the app. The sidebar will say that a token is in use (the token itself is never displayed).

`.env` and `.streamlit/secrets.toml` are listed in `.gitignore`, so Git ignores them. Before committing, run `git status` and `git diff --cached` to double-check. If a token is ever committed by accident, revoke it on GitHub immediately.

## Testing

```bash
python -m pytest -v
```

The tests mock all API calls, so they need no internet connection or GitHub account. They cover language percentages, empty data, sorting, totals, missing descriptions, empty repository lists, date formatting, input validation and the API error handling.

## Known limitations

- Only **public** data is shown. Private repositories and private activity are never available.
- Language statistics measure **bytes of code that GitHub detects**, not time spent coding or skill. Language data is requested for a limited number of repositories (20 by default, most recently pushed first, forks excluded unless you opt in), because each one costs an API request.
- At most the first 500 repositories are fetched. If a user has more, the app says so and totals cover only the fetched repositories.
- The Events API returns only recent public events (at most 300 from the last 90 days; this app loads the latest 100). It is **not** a full contribution history and cannot provide exact contribution counts.
- Stars, forks and sizes are summed over the fetched repositories, forks included, and the "size" is GitHub's storage size, not lines of code.
- Data is cached for a few minutes (profile, repositories, events) or an hour (languages), so very recent changes may not appear until you press **Refresh data**.
- The page font is loaded from Google Fonts. Offline, the app falls back to a system font.

## Future improvements

- Contribution calendar and commit statistics using GitHub's GraphQL API (requires a token).
- Compare two profiles side by side.
- Export tables and charts.
- Dark theme.
- Continuous integration that runs the tests on every push.
- Deployment to Streamlit Community Cloud, with the token stored in its secrets manager.

## License

The [MIT License](https://choosealicense.com/licenses/mit/) is recommended for a portfolio project: it is short, permissive and widely understood. To use it, create a file named `LICENSE` in the project root, paste the MIT license text from the link above, and replace the year and name with your own.

## Author

**Shrinivas** - [github.com/shrinivasnaik27](https://github.com/shrinivasnaik27)
