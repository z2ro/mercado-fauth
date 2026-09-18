FROM mcr.microsoft.com/playwright/python:v1.58.0-noble
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY scripts ./scripts
RUN U2NET_HOME=/opt/rembg python scripts/download_background_model.py
ENV BANNER_BACKGROUND_MODEL=/opt/rembg/u2netp.onnx
ENV NUMBA_CACHE_DIR=/tmp/numba
COPY backend ./backend
COPY assets ./assets
COPY examples ./examples
RUN mkdir -p output
EXPOSE 8000
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
