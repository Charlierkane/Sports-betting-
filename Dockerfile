# Slim Python with system libs for Playwright/Chromium
FROM python:3.10-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# System deps needed by Chromium
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libdrm2 libgbm1 libasound2 \
    libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libcups2 libxrandr2 \
    libglib2.0-0 libgtk-3-0 libpango-1.0-0 libpangocairo-1.0-0 libatspi2.0-0 \
    wget fonts-liberation libappindicator3-1 xdg-utils ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python deps
COPY requirements.txt /app/requirements.txt
RUN pip install -U pip && pip install -r requirements.txt

# Install Playwright browser binaries
RUN python -m playwright install chromium

# App code
COPY . /app

EXPOSE 8000
CMD ["sh","-c","streamlit run app.py --server.port $PORT --server.address 0.0.0.0"]
