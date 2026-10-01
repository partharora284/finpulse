# FinPulse

**Automated Financial Market Data Pipeline & Statistical Drift Monitor**

FinPulse is a fully decoupled, multi-container open-source data pipeline that
automatically ingests financial time-series data, computes rolling
volatility metrics, persists them in PostgreSQL, and exposes an interactive
Streamlit dashboard with automated **regime drift detection** via the
two-sample **Kolmogorov–Smirnov (KS)** test.

Built as a mini-project for *Open Source Tools for Data Science (OST)*,
demonstrating containerization, Docker Compose orchestration, modular
service design, and reproducible open-source tooling end-to-end.

---

## Architecture

```
                         ┌───────────────────────────────────────────┐
                         │              docker-compose.yml             │
                         │            (finpulse_net bridge)            │
                         └───────────────────────────────────────────┘

     ┌────────────────────┐        ┌────────────────────┐        ┌────────────────────┐
     │      ingestor       │        │         db          │        │      dashboard       │
     │  Python 3.11 worker │  SQL   │  postgres:16-alpine  │  SQL   │  Streamlit :8501     │
     │──────────────────── │──────▶│──────────────────── │◀──────│──────────────────── │
     │ • yfinance fetch     │ upsert │ ohlcv_metrics table  │  read  │ • Metric cards        │
     │ • daily returns      │        │ UNIQUE(ticker,date)  │        │ • Plotly price/vol    │
     │ • 20D ann. volatility│        │ finpulse_pgdata vol. │        │ • KS drift detector   │
     │ • scheduled polling  │        │ healthcheck:pg_isready│       │ • Regime alert banner │
     └────────────────────┘        └────────────────────┘        └────────────────────┘
              │                              ▲                              │
              │      depends_on: healthy     │      depends_on: healthy      │
              └──────────────────────────────┴──────────────────────────────┘

     External:  Yahoo Finance API  ──▶  ingestor            Browser  ◀──  dashboard (:8501)
```

**Data flow:** `yfinance` → `ingestor` (transform: returns + rolling
volatility) → `PostgreSQL` (idempotent upsert) → `dashboard` (query, KS-test,
visualize).

---

## Services

| Service     | Image / Base           | Role                                                                 |
|-------------|-------------------------|-----------------------------------------------------------------------|
| `db`        | `postgres:16-alpine`    | Relational store; named volume `finpulse_pgdata`; `pg_isready` healthcheck |
| `ingestor`  | `python:3.11-slim`      | Scheduled worker: fetch → compute metrics → idempotent upsert          |
| `dashboard` | `python:3.11-slim`      | Streamlit app on `:8501`: metrics, Plotly charts, KS drift alerts      |

---

## Quick Start

### Prerequisites
- Docker Engine ≥ 24.x
- Docker Compose v2 (`docker compose`, bundled with modern Docker Desktop / Docker CE)

### 1. Clone and configure

```bash
git clone https://github.com/<your-org>/finpulse.git
cd finpulse
cp .env.example .env
# Edit .env to adjust tickers, credentials, or scheduling as needed
```

### 2. Launch the stack

```bash
docker compose up --build
```

This will:
1. Start `db` and wait for it to report healthy via `pg_isready`.
2. Start `ingestor`, which creates the schema, fetches historical OHLCV data
   for the configured tickers (default `^NSEI,SPY`), computes daily returns
   and 20-day annualized rolling volatility, and upserts into PostgreSQL.
3. Start `dashboard`, serving the Streamlit UI at **http://localhost:8501**.

### 3. Run in the background

```bash
docker compose up -d --build
docker compose logs -f ingestor   # watch ingestion progress
```

### 4. Tear down

```bash
docker compose down          # stop containers, keep data volume
docker compose down -v       # stop containers AND remove finpulse_pgdata
```

---

## Windows Setup Guide (Docker Desktop + Git Bash)

FinPulse was developed and tested cross-platform, but Windows users
following along in **Git Bash** run into a couple of environment-specific
speed bumps that are worth calling out explicitly.

### Step-by-step

1. **Install and start Docker Desktop.** Download it from
   [docker.com](https://www.docker.com/products/docker-desktop/), install
   it, and make sure it is actually **running** (look for the whale icon
   in your system tray) before doing anything else. Every command below
   will fail with a connection error if Docker Desktop's engine isn't up.
2. **Open Git Bash** and clone/`cd` into the project:
   ```bash
   cd /c/Users/<you>/projects
   git clone https://github.com/<your-org>/finpulse.git
   cd finpulse
   ```
3. **Create your local `.env` file:**
   ```bash
   cp .env.example .env
   ```
   Git Bash on Windows supports `cp` natively, so this works exactly like
   on macOS/Linux — no need for `copy` or PowerShell syntax here.
4. **Build and start the stack:**
   ```bash
   docker compose up --build
   ```
   The first run downloads the `postgres:16-alpine` image and builds the
   `ingestor`/`dashboard` images from scratch, so it can take a few
   minutes depending on your connection — this is normal.
5. **Open the dashboard** in your browser once containers report healthy:
   [http://localhost:8501](http://localhost:8501)
6. **Watch ingestion progress** (optional, in a second Git Bash window):
   ```bash
   docker compose logs -f ingestor
   ```
7. **Stop everything when done:**
   ```bash
   docker compose down
   ```

### Common Windows-specific errors and fixes

| Error you see | What's happening | Fix |
|---|---|---|
| `error during connect: this error may indicate that the docker daemon is not running` | Docker Desktop's engine isn't started yet | Open Docker Desktop from the Start Menu, wait for the whale icon to stop animating, then re-run the command |
| `Ports are not available: exposing port TCP 0.0.0.0:5432` | Another program (often a local Postgres/SQL Server install) is already using that port | Stop the conflicting local service, or change the host-side port in `docker-compose.yml`, e.g. `"5433:5432"` |
| `error: subprocess-exited-with-error` mentioning `meson-python`, `vswhere.exe`, or `Microsoft Visual C++ 14.0 is required` | `pip` tried to **compile** a package (like `pandas`) from source instead of downloading a pre-built wheel — this only happens if you run `pip install -r requirements.txt` **natively on Windows**, not inside Docker | You should not need MSVC build tools at all for the normal `docker compose up` workflow, since installation happens inside the Linux container. If you're intentionally installing natively (see the section below), make sure you're on the updated `requirements.txt` (version ranges, not exact pins) and have a recent `pip` (`python -m pip install --upgrade pip`) — recent `pip` versions are much better at finding a matching pre-built wheel |
| Git Bash: `docker: command not found` | Docker Desktop's CLI isn't on your Git Bash `PATH` yet | Restart Git Bash (and your terminal/computer if needed) after installing Docker Desktop; also confirm "Use the WSL 2 based engine" is enabled in Docker Desktop settings |
| Dashboard loads but shows "No data found yet" indefinitely | The `ingestor` container hasn't completed its first fetch | Check `docker compose logs ingestor`; on some restricted networks yfinance requests may be blocked by a corporate firewall/VPN — try a different network |

---

## Running a Service Natively, Without Docker (Quick Local Debugging)

Sometimes you want to iterate on `app.py` or `scheduler.py` directly with
your normal Python tooling (breakpoints, a linter, fast reload) instead of
rebuilding a container on every change. FinPulse supports this as a
secondary workflow, while keeping Docker Compose as the primary,
intended-for-grading architecture.

1. **Start only the database** via Compose (it still publishes port 5432
   to your host):
   ```bash
   docker compose up -d db
   ```
2. **Confirm `.env` has `POSTGRES_HOST=localhost`** (this is the shipped
   default in `.env.example` — see the comment above that variable for
   why). You do not need to change anything for this to work; it's only
   `docker compose up`'s own container environment that overrides this
   value back to `db` for the containerized services.
3. **Create a virtual environment and install one service's dependencies:**
   ```bash
   cd dashboard
   python -m venv .venv
   source .venv/Scripts/activate      # Git Bash on Windows
   # source .venv/bin/activate        # macOS/Linux
   python -m pip install --upgrade pip
   pip install -r requirements.txt
   ```
4. **Run it directly:**
   ```bash
   streamlit run app.py
   ```
   or, from `ingestor/` with its own venv/requirements:
   ```bash
   python scheduler.py
   ```
   Both `app.py` and `scheduler.py`/`db_utils.py` call `load_dotenv()` on
   startup, so they automatically pick up the same `.env` file at the
   project root — you do not need to export environment variables by hand.

This mode talks to the exact same Postgres data your containerized
pipeline uses, since it's the same `db` container and the same
`finpulse_pgdata` volume — just reached via `localhost:5432` instead of
Docker's internal network.

---

## Configuration (`.env`)

| Variable                  | Default        | Description                                       |
|----------------------------|----------------|----------------------------------------------------|
| `POSTGRES_USER`             | `finpulse`     | Database user                                       |
| `POSTGRES_PASSWORD`         | `finpulse_pw`  | Database password                                   |
| `POSTGRES_DB`                | `finpulse_db`  | Database name                                        |
| `POSTGRES_HOST`              | `localhost`    | DB hostname. Only matters for native runs — Docker Compose overrides this to `db` automatically for the `ingestor`/`dashboard` containers (see `docker-compose.yml`) |
| `POSTGRES_PORT`              | `5432`         | DB port, published to the host by the `db` service   |
| `TICKERS`                    | `^NSEI,SPY`    | Comma-separated Yahoo Finance tickers                |
| `LOOKBACK_PERIOD`            | `2y`           | yfinance history window (`6mo`, `1y`, `2y`, ...)     |
| `VOLATILITY_WINDOW`          | `20`           | Rolling window (days) for annualized volatility      |
| `POLL_INTERVAL_MINUTES`      | `1440`         | Ingestor re-fetch interval                            |
| `DRIFT_RECENT_WINDOW`        | `20`           | KS test "recent" sample size (days)                   |
| `DRIFT_BASELINE_WINDOW`      | `60`           | KS test "baseline" sample size (days)                 |
| `DRIFT_P_VALUE_THRESHOLD`    | `0.05`         | Significance threshold for drift alerts                |

---

## General Troubleshooting

| Symptom | Likely Cause & Fix |
|---|---|
| `Could not connect to the PostgreSQL database after multiple retries` (dashboard) | `db` isn't healthy yet, or credentials in `.env` don't match what Postgres was first initialized with. Run `docker compose ps` to check container health. Postgres only applies `POSTGRES_PASSWORD` on the **first-ever** initialization of its data volume — if you changed the password after that, run `docker compose down -v` to reset the volume and re-initialize |
| `ModuleNotFoundError: No module named 'plotly'` / `'sqlalchemy'` / `'psycopg2'` when running locally | You're running `app.py` or `scheduler.py` natively but installed the wrong service's `requirements.txt`, or installed into the wrong (or no) virtual environment | Activate the venv for the specific service folder you're in, then `pip install -r requirements.txt` from inside that folder (`dashboard/` or `ingestor/`) |
| yfinance fetch fails repeatedly | Transient Yahoo Finance outage, rate limiting, or restricted network/firewall | Check `docker compose logs ingestor` for the specific exception logged before each retry; try again later or on a different network |
| Port `8501` (or `5432`) already in use | Another process on your machine is bound to that port | Stop the conflicting process, or remap the host-side port in `docker-compose.yml`, e.g. `"8502:8501"` |
| Code changes aren't reflected after editing | Compose reuses previously built images by default | Re-run with `docker compose up --build` to force a rebuild |
| `Insufficient history for drift analysis` message in the UI | Not a bug — the KS test needs at least `(DRIFT_RECENT_WINDOW + DRIFT_BASELINE_WINDOW)` clean daily-return observations before it can run meaningfully | Wait for more days of history to accumulate, or reduce the window sizes in the dashboard sidebar |

---

## Statistical Methodology

- **Daily return:** simple percentage change of adjusted close price.
- **Rolling volatility:** `std(daily_return, window=20) * sqrt(252)` — annualized.
- **Drift detection:** `scipy.stats.ks_2samp` compares the empirical
  distribution of the most recent *N* days of returns against the *M* days
  immediately preceding them. The KS test is non-parametric, making it
  robust to the fat-tailed, non-Gaussian nature of financial returns. A
  p-value below the configured threshold (default `0.05`) flags a
  **regime drift alert** in the dashboard.

---

## Repository Structure

```
finpulse/
├── .github/workflows/lint.yml   # CI: flake8 + black on every push/PR
├── docker-compose.yml           # db + ingestor + dashboard orchestration
├── .env.example                 # Configuration template
├── .gitignore
├── LICENSE                      # MIT
├── README.md
├── ingestor/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── scheduler.py              # Scheduled ingest loop
│   └── db_utils.py               # Connection, schema, idempotent upserts
└── dashboard/
    ├── Dockerfile
    ├── requirements.txt
    ├── app.py                    # Streamlit UI
    └── drift_detector.py         # KS two-sample drift module
```

---

## Rubric Alignment

| Rubric Criterion            | How FinPulse Satisfies It                                                                 |
|-------------------------------|-----------------------------------------------------------------------------------------------|
| **Containerization**           | Every service ships its own slim, non-root `Dockerfile` with pinned base images                |
| **Multi-service orchestration**| Single `docker-compose.yml` wires `db` → `ingestor` → `dashboard` via `depends_on` + healthchecks |
| **Modularity**                 | Clear separation of concerns: `db_utils.py` (persistence), `scheduler.py` (ETL), `drift_detector.py` (stats), `app.py` (presentation) |
| **Reproducibility**            | Bounded dependency version ranges (floor + ceiling, wheel-friendly across OSes), `.env.example` template, named persistent volume, idempotent upserts |
| **Git hygiene**                 | `.gitignore`, MIT `LICENSE`, CI lint workflow (`flake8` + `black`), clean directory layout       |
| **Error handling / resilience**| Retry-with-backoff on DB connections and yfinance fetches; per-ticker failure isolation           |
| **Open-source tooling**        | PostgreSQL, yfinance, pandas, SQLAlchemy, Streamlit, Plotly, SciPy — all OSS                      |

---

## License

Released under the [MIT License](LICENSE).
