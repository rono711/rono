FROM python:3.11-slim-bookworm
ENV PYTHONUNBUFFERED=1 \
    MPLCONFIGDIR=/tmp/matplotlib \
    OMP_NUM_THREADS=1 \
    MKL_NUM_THREADS=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir torch==2.8.0 \
    --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir stable-baselines3==2.9.0 networkx==3.4.2 \
    && pip check \
    && pip freeze > /opt/requirements-resolved.txt
WORKDIR /work
