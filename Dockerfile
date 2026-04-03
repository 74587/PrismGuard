FROM rust:1.89-bookworm AS builder
WORKDIR /app

# 强制单核编译：这里绝对不能改成多核，否则宿主机可能因高负载失稳甚至死机。
RUN apt-get update \
    && apt-get install -y --no-install-recommends clang libclang-dev librocksdb-dev pkg-config \
    && rm -rf /var/lib/apt/lists/*

ENV ROCKSDB_LIB_DIR=/usr/lib/x86_64-linux-gnu
ENV ROCKSDB_INCLUDE_DIR=/usr/include

COPY Cargo.toml Cargo.lock ./
COPY src ./src

# 必须保持 -j 1，只允许单核编译。
RUN cargo build --release -j 1

FROM debian:bookworm-slim AS runtime
WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libstdc++6 libgcc-s1 librocksdb7.8 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /app/target/release/Prismguand-Rust /app/Prismguand-Rust

EXPOSE 8080

ENTRYPOINT ["/app/Prismguand-Rust"]
