# ── Stage 1: compilación ──────────────────────────────────────────
FROM nvidia/cuda:12.3.2-devel-ubuntu22.04 AS builder

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    git build-essential cmake curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN git clone https://github.com/ggerganov/llama.cpp.git

WORKDIR /app/llama.cpp

RUN cmake -B build \
    -DGGML_CUDA=ON \
    -DCMAKE_CUDA_ARCHITECTURES=75 \
    -DCMAKE_EXE_LINKER_FLAGS="-Wl,--allow-shlib-undefined"

RUN cmake --build build --config Release -j$(nproc) --target llama-server

# ── Stage 2: runtime ─────────────────────────────────────────────
FROM nvidia/cuda:12.3.2-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Solo copiamos el binario y las libs necesarias
COPY --from=builder /app/llama.cpp/build/bin/llama-server .
COPY --from=builder /app/llama.cpp/build/bin/lib*.so* ./lib/

ENV LD_LIBRARY_PATH=/app/lib

EXPOSE 8080

ENTRYPOINT ["/app/llama-server"]
