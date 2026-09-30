# Toolchain for building ZSA firmware and drawing keymaps.
# Matches the GitHub Actions runner (Ubuntu 24.04, arm-none-eabi-gcc 13.2),
# so a local `make docker-build` produces the same firmware as CI.
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      binutils-arm-none-eabi gcc-arm-none-eabi libnewlib-arm-none-eabi \
      dfu-util git make python3 python3-venv ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
RUN python3 -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir --upgrade pip setuptools wheel \
 && /opt/venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt

# The repo is mounted from the host and may be owned by a different uid.
RUN git config --system --add safe.directory '*'

ENV PATH=/opt/venv/bin:$PATH
WORKDIR /work
