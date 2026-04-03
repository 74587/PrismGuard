# Rust Master GHCR Publish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 Rust `master` 新增 Docker 镜像构建和 GHCR 自动发布能力，`push` 到 `master` 自动发布镜像，并支持手动触发补跑。

**Architecture:** 在仓库根目录新增多阶段 `Dockerfile` 和最小 `.dockerignore`，在 `.github/workflows/ghcr.yml` 中串联 Rust 构建校验、Docker Buildx 构建和 GHCR 推送。工作流只保留 `push master` 和 `workflow_dispatch`，标签固定为 `latest`、`master`、`sha-*`，首版仅支持 `linux/amd64`。

**Tech Stack:** Rust, Docker, GitHub Actions, GHCR, docker/build-push-action, docker/metadata-action

---

### Task 1: 新增容器构建文件

**Files:**
- Create: `Dockerfile`
- Create: `.dockerignore`
- Test: `docker build -t prismguard:test .`

- [ ] **Step 1: 写失败检查，确认仓库当前没有容器构建文件**

Run: `test -f Dockerfile && test -f .dockerignore`
Expected: FAIL because files do not exist yet

- [ ] **Step 2: 新增最小多阶段 Dockerfile**

```dockerfile
FROM rust:1.89-bookworm AS builder
WORKDIR /app

COPY Cargo.toml Cargo.lock ./
COPY src ./src

RUN cargo build --release -j 1

FROM debian:bookworm-slim AS runtime
WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libstdc++6 libgcc-s1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /app/target/release/Prismguand-Rust /app/Prismguand-Rust

EXPOSE 8080

ENTRYPOINT ["/app/Prismguand-Rust"]
```

- [ ] **Step 3: 新增最小 `.dockerignore`**

```gitignore
.git
.github
target
artifacts
docs
.env
.env.*
```

- [ ] **Step 4: 运行文件存在性检查，确认通过**

Run: `test -f Dockerfile && test -f .dockerignore`
Expected: PASS

### Task 2: 新增 GHCR workflow

**Files:**
- Create: `.github/workflows/ghcr.yml`
- Test: `python - <<'PY' ...`

- [ ] **Step 1: 写失败检查，确认 workflow 还不存在**

Run: `test -f .github/workflows/ghcr.yml`
Expected: FAIL because workflow file does not exist yet

- [ ] **Step 2: 新增 GHCR workflow**

```yaml
name: ghcr

on:
  push:
    branches:
      - master
  workflow_dispatch:

permissions:
  contents: read
  packages: write

env:
  IMAGE_NAME: ghcr.io/cassiopeiacode/prismguard

jobs:
  build-and-publish:
    runs-on: ubuntu-latest

    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Install Rust toolchain
        uses: dtolnay/rust-toolchain@stable

      - name: Cache cargo registry
        uses: Swatinem/rust-cache@v2

      - name: Verify Rust release build
        run: cargo build --release -j 1

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Log in to GHCR
        uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Extract Docker metadata
        id: meta
        uses: docker/metadata-action@v5
        with:
          images: ${{ env.IMAGE_NAME }}
          tags: |
            type=raw,value=latest,enable={{is_default_branch}}
            type=raw,value=master,enable={{is_default_branch}}
            type=sha,prefix=sha-

      - name: Build and push
        uses: docker/build-push-action@v6
        with:
          context: .
          file: ./Dockerfile
          platforms: linux/amd64
          push: true
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
```

- [ ] **Step 3: 运行存在性检查，确认 workflow 已创建**

Run: `test -f .github/workflows/ghcr.yml`
Expected: PASS

### Task 3: 静态验证与远端可用性检查

**Files:**
- Verify: `Dockerfile`
- Verify: `.github/workflows/ghcr.yml`

- [ ] **Step 1: 检查 YAML 和关键字段**

Run:

```bash
python - <<'PY'
from pathlib import Path
workflow = Path('.github/workflows/ghcr.yml').read_text(encoding='utf-8')
assert 'ghcr.io/cassiopeiacode/prismguard' in workflow
assert 'push:' in workflow
assert 'workflow_dispatch:' in workflow
assert 'pull_request:' not in workflow
assert 'docker/build-push-action@v6' in workflow
assert 'cargo build --release' in workflow
assert 'push: true' in workflow
print('workflow-ok')
PY
```

Expected: PASS with `workflow-ok`

- [ ] **Step 2: 检查 Dockerfile 关键内容**

Run:

```bash
python - <<'PY'
from pathlib import Path
dockerfile = Path('Dockerfile').read_text(encoding='utf-8')
assert 'FROM rust:' in dockerfile
assert 'cargo build --release' in dockerfile
assert 'FROM debian:' in dockerfile
assert 'Prismguand-Rust' in dockerfile
assert 'ENTRYPOINT ["/app/Prismguand-Rust"]' in dockerfile
print('dockerfile-ok')
PY
```

Expected: PASS with `dockerfile-ok`

- [ ] **Step 3: 推送到 `master` 或手动触发 workflow 做远端验证**

Run: `git push origin master` 或在 GitHub Actions 页面手动点击 `workflow_dispatch`
Expected:
- GitHub Actions 中 `ghcr` workflow 成功
- GHCR 中出现 `latest`、`master`、`sha-*` 标签
