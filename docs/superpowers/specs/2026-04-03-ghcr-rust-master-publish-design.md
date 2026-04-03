# Rust Master GHCR 自动发布设计

## 背景

当前仓库的远端分支已经调整为：

- `master`：Rust 主线
- `python`：原 Python 历史保留分支

现阶段需要为 Rust `master` 增加自动镜像构建与发布能力，让 GitHub Actions 在 Rust 主线更新后自动将镜像发布到 GHCR，便于后续直接拉取和部署。

当前仓库现状：

- 有 `Cargo.toml`、`Cargo.lock`、`src/main.rs`
- 有本地启动脚本 `start.sh`
- 没有现成的 `.github/workflows/`
- 没有现成的 `Dockerfile`
- 没有现成的容器发布流程

## 目标

本阶段目标是为 Rust `master` 建立最小可用、稳定、可验证的 GHCR 自动发布闭环。

要求如下：

- 仅针对 Rust `master` 自动推送镜像
- 镜像发布到 `ghcr.io/cassiopeiacode/prismguard`
- 标签至少包含：
  - `latest`
  - `master`
  - `sha-<commit>`
- 首版只支持 `linux/amd64`
- 容器直接运行 release 二进制，不依赖 `start.sh`
- workflow 中需要显式验证 Rust 项目必须以单核 `-j 1` 成功构建，避免系统因并发编译失稳

## 非目标

本阶段明确不做：

- `python` 分支镜像发布
- 多平台镜像矩阵，例如 `linux/arm64`
- 发布签名、SBOM、provenance
- 自动创建 GitHub Release
- 部署到 Kubernetes、Fly.io、Railway 等平台
- 运行时 `.env` 模板补全
- 训练、审核、样本 RPC 相关 CI

## 推荐方案

采用“多阶段 Docker 构建 + 单一 GitHub Actions workflow”的最小发布方案。

### 方案摘要

- 新增 `Dockerfile`
- 新增 `.github/workflows/ghcr.yml`
- `push` 到 `master` 时：
  - 执行 Rust 构建验证
  - 构建容器镜像
  - 登录 GHCR
  - 推送镜像标签
- `workflow_dispatch` 手动触发时：
  - 执行同一套 Rust 构建验证
  - 构建并推送镜像

这是当前阶段最合适的方案，因为它：

- 与现有仓库结构匹配
- 不要求预先建立复杂发布体系
- 能马上产出可部署的 GHCR 镜像
- 后续容易扩展为多平台或 release 驱动流程

## 备选方案

### 方案 A：只做 Rust 编译，不做镜像发布

优点：

- 改动最小
- 调试简单

缺点：

- 不满足“自动编译到 GHCR”的目标
- 仍需手工构建和上传镜像

### 方案 B：首版即做多平台发布

优点：

- 功能更完整

缺点：

- 会引入更多 CI 时间和缓存复杂度
- 当前仓库还没有容器基础设施，第一版收益不高

## 设计细节

## 1. 镜像构建

新增仓库根目录 `Dockerfile`，采用多阶段构建。

### Builder 阶段

强制要求：所有编译必须单核执行（`-j 1`），禁止多核并发编译，否则可能导致宿主机负载失控甚至死机。

- 基于官方 Rust 构建镜像
- 复制 `Cargo.toml`、`Cargo.lock`、`src/`
- 执行 `cargo build --release -j 1`

### Runtime 阶段

- 使用精简 Linux 基础镜像
- 仅复制 release 二进制
- 设置工作目录为 `/app`
- 暴露应用常用端口
- 容器入口直接执行二进制

这样做的目的：

- 减小最终镜像体积
- 避免把编译工具链带入运行镜像
- 使启动方式与 CI/部署一致

## 2. workflow 触发规则

新增 `.github/workflows/ghcr.yml`。

触发规则：

- `push` 到 `master`
- `workflow_dispatch`

语义要求：

- `push` 到 `master` 自动推送 GHCR
- `workflow_dispatch` 用于手动补跑、补发或验证
- 不保留 `pull_request` 工作流，避免额外消耗 runner 资源

## 3. 镜像标签规则

发布镜像名固定为：

- `ghcr.io/cassiopeiacode/prismguard`

首版标签规则：

- `latest`
- `master`
- `sha-<完整或短 commit sha>`

其中：

- `latest` 用于默认拉取
- `master` 用于分支语义固定引用
- `sha-*` 用于精确回溯

## 4. GitHub 权限与登录

workflow 需要：

- `contents: read`
- `packages: write`

登录方式使用 GitHub Actions 内建的：

- `github.actor`
- `secrets.GITHUB_TOKEN`

这样无需手工配置独立 GHCR token。

## 5. CI 校验顺序

workflow 内的推荐顺序：

单核约束：CI 中所有 Cargo 编译命令必须固定使用 `-j 1`。

1. checkout
2. 安装 Rust toolchain
3. 执行 `cargo build --release -j 1`
4. 设置 Docker Buildx
5. 生成镜像 metadata/tag
6. 登录 GHCR
7. 构建并推送镜像

首版不强制把完整测试矩阵塞进发布 workflow。另一个硬性要求是任何后续新增编译步骤也必须保持单核。，避免把镜像发布与长时间测试强耦合。

## 6. 运行时入口

容器入口直接执行 Rust 二进制，而不是调用 `start.sh`。

原因：

- `start.sh` 适合本地手工运行
- 容器内不需要再做本地存在性检查
- CI/CD 场景下应保证入口最短、最确定

如果后续需要：

- 环境变量预处理
- 启动前目录准备
- 信号转发增强

再单独引入 entrypoint 脚本。

## 7. 远端验证策略

实现完成后，验证以 GitHub Actions 远端执行为主：

- 推送到 `master` 触发 workflow
- 检查 GitHub Actions 运行结果
- 检查 GHCR 仓库中是否出现新镜像标签
- 必要时使用 `workflow_dispatch` 手动补跑

## 风险与约束

### 1. 二进制名风险

当前包名为 `Prismguand-Rust`，镜像构建时必须使用正确的 release 二进制路径，不能假设为通用 `app` 名称。

### 2. 运行时依赖风险

如果 `rocksdb` 或系统库在运行镜像中需要动态依赖，运行阶段基础镜像必须包含相应共享库。

首版实现时应优先选择对 Rust + RocksDB 更稳妥的运行镜像，而不是盲目追求最小体积。

### 3. 权限风险

如果仓库 Actions 或 Packages 权限被组织策略限制，workflow 可能构建成功但推送 GHCR 失败。

这属于远端仓库权限配置问题，不应通过放宽 workflow 逻辑规避。

## 落地范围

本阶段实际文件变更范围应控制为：

- 新增 `Dockerfile`
- 新增 `.github/workflows/ghcr.yml`
- 如有必要，补一个 `.dockerignore`
- 如有必要，补少量 README/发布说明

不应在本阶段顺手引入：

- 复杂部署编排
- 额外 release workflow
- 无关代码重构

## 验收标准

满足以下条件视为完成：

1. `master` 上新增 workflow 与 Dockerfile
2. `push` 到 `master` 时能把镜像推到 `ghcr.io/cassiopeiacode/prismguard`
3. `workflow_dispatch` 可手动触发同一发布流程
4. GHCR 中可见 `latest`、`master`、`sha-*` 标签
5. GitHub Actions 日志能证明镜像构建并推送成功
