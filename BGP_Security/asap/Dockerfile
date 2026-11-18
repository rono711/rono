FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    MPLCONFIGDIR=/tmp/matplotlib \
    OMP_NUM_THREADS=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       build-essential graphviz \
    && rm -rf /var/lib/apt/lists/*

COPY . /opt/aspa_eval

RUN python -m pip install --no-cache-dir \
      --report /opt/aspa-install-report.json /opt/aspa_eval \
    && python -m pip check \
    && python -m pip freeze > /opt/aspa-requirements-resolved.txt

WORKDIR /work
