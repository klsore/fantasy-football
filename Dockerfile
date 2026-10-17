FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py ./
COPY intelligence.py ./
COPY ANALYSIS_PROMPT.md ./
COPY accounts.py snapshots.py refresh.py webapp.py management.py schedule.py ./
COPY public ./public
RUN useradd --create-home app && mkdir /app/data /app/state /snapshots && chown app:app /app/data /app/state /snapshots
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request,os; urllib.request.urlopen(urllib.request.Request('http://localhost:8000/api/health',headers={'Host':os.getenv('PRIVATE_HOST') if os.getenv('SITE_MODE')=='private' else os.getenv('PUBLIC_HOST','fantasy.localhost')}))"
CMD ["gunicorn", "webapp:create_app()", "--no-control-socket", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "4", "--timeout", "150"]
