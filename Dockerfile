# ── AML Investigation Agent — Dockerfile ──────────────────────────────────────
#
# Builds a single image that can run as:
#   - The agent CLI:     docker run aml-agent python run_agent.py --alert ALT-003
#   - The Streamlit UI:  docker run -p 8501:8501 aml-agent streamlit run ui/streamlit_app.py
#
# Build:
#   docker build -t aml-agent .
#
# Run agent:
#   docker run --env-file .env aml-agent python run_agent.py --list-alerts
#
# Run UI:
#   docker run --env-file .env -p 8501:8501 aml-agent \
#     streamlit run ui/streamlit_app.py --server.address 0.0.0.0

FROM python:3.12-slim

# ── System deps ────────────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# ── Working directory ──────────────────────────────────────────────────────────
WORKDIR /app

# ── Python deps — cached layer ─────────────────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Application code ───────────────────────────────────────────────────────────
COPY . .

# ── Seed the database at build time ────────────────────────────────────────────
# This bakes the mock data into the image so the container works without
# any additional setup steps. Override DB_PATH env var to use external storage.
RUN python data/seed_data.py

# ── Default command: list available alerts ─────────────────────────────────────
CMD ["python", "run_agent.py", "--list-alerts"]
