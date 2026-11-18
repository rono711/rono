FROM ubuntu:22.04@sha256:0a9887931b0e38af2e3487ed39e29436b0535c4f3f52e57745baaf79d1898956

ENV DEBIAN_FRONTEND=noninteractive
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential cmake git curl ca-certificates \
    python3 python3-pip python3-venv \
    libasio-dev libtinyxml2-dev libssl-dev \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/build-tools \
    && /opt/build-tools/bin/pip install --no-cache-dir \
       colcon-common-extensions vcstool

ENV PATH="/opt/build-tools/bin:${PATH}"
WORKDIR /opt/fastdds
CMD ["bash"]

ENV CMAKE_BUILD_PARALLEL_LEVEL=2

RUN curl -fL --retry 3 \
      https://raw.githubusercontent.com/eProsima/Fast-DDS/v3.6.1/fastdds.repos \
      -o fastdds.repos \
    && mkdir -p src \
    && vcs import src < fastdds.repos \
    && vcs export --exact src > source-lock.repos \
    && pip freeze > build-tools-lock.txt

RUN colcon build \
    --packages-up-to fastdds \
    --parallel-workers 2 \
    --event-handlers console_direct+ \
    --cmake-args -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF
