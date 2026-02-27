FROM pytorch/pytorch:2.6.0-cuda12.6-cudnn9-runtime

# System deps
RUN apt-get update -qq && \
    apt-get install -y -qq git build-essential && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Clone BoltzGen at pinned commit and install
RUN git clone -q https://github.com/HannesStark/boltzgen /root/boltzgen && \
    cd /root/boltzgen && \
    git checkout -q 247b9bbd8b68a60aba854c2968d6a0cddd21ad6d && \
    pip install --no-cache-dir -e .

WORKDIR /root
