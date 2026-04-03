FROM ubuntu:24.04 AS builder
WORKDIR /app

# 强制单核编译：这里绝对不能改成多核，否则宿主机可能因高负载失稳甚至死机。
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential ca-certificates clang curl libclang-dev librocksdb-dev pkg-config \
    && rm -rf /var/lib/apt/lists/*

ENV RUSTUP_HOME=/usr/local/rustup
ENV CARGO_HOME=/usr/local/cargo
ENV PATH=/usr/local/cargo/bin:${PATH}

RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
    | sh -s -- -y --profile minimal --default-toolchain 1.89.0

ENV ROCKSDB_LIB_DIR=/usr/lib/x86_64-linux-gnu
ENV ROCKSDB_INCLUDE_DIR=/usr/include

COPY Cargo.toml Cargo.lock ./
COPY vendor ./vendor
COPY src ./src

# 必须保持 -j 1，只允许单核编译。
RUN cargo build --release -j 1

FROM ubuntu:24.04 AS runtime
WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libstdc++6 libgcc-s1 librocksdb-dev \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /app/target/release/Prismguand-Rust /app/Prismguand-Rust

EXPOSE 8080

ENTRYPOINT ["/app/Prismguand-Rust"]
