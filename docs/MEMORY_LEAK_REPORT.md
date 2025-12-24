# 长时间连续运行内存泄漏/爆内存审计报告（PrismGuard）

生成时间：2025-12-24  
范围：仅基于仓库源码静态审计（未运行压测/未接入真实上游）

---

## 结论摘要（按严重级别排序）

### P0（确定性无界增长，满足“会且一定会导致长时间连续运行内存泄漏/爆内存”）
1) **SSE 解析缓冲无上限：[`SSEBufferTransformer.feed()`](ai_proxy/proxy/stream_transformer.py:46) 的 `self.buffer` 可无限增长**
- 关键证据：
  - 累加：[`self.buffer += text`](ai_proxy/proxy/stream_transformer.py:52)
  - 仅在存在分隔符时消费：[`while "\n\n" in self.buffer`](ai_proxy/proxy/stream_transformer.py:55)
- 触发条件（任何一种均可）：
  - 上游返回的流式内容缺失 `\n\n` 分隔符（协议异常/网关篡改/实现差异）
  - 恶意上游/中间人持续发送无分隔符数据（典型 DoS）
  - 备注：即使“上游可信 + 下游超时会断开”，只要在超时窗口内持续接收且缺少 `\n\n`，`self.buffer` 依旧会单调增长；区别只是增长窗口从“无限”变成“超时窗口内”。
- 结果：
  - `self.buffer` 严格单调增长，进程持续运行时内存必然上升直至 OOM（“确定性爆内存”）

### P1（高危内存峰值/驻留：长期运行下极易演化为“RSS 不回落 + swap 持续升高”）
2) **BoW 训练在“欠采样”策略下可能一次性加载全量样本到内存（样本量大时必然产生巨大峰值）**
- 关键证据（训练入口）：
  - 调度触发训练：[`await asyncio.to_thread(train_local_model, profile)`](ai_proxy/moderation/smart/scheduler.py:122)
  - BoW 训练：[`train_bow_model()`](ai_proxy/moderation/smart/bow.py:111)
- 关键证据（导致峰值的加载策略）：
  - 欠采样逻辑会先加载“每类全部样本”：[`pass_samples = self._load_samples_by_label(0, pass_count)`](ai_proxy/moderation/smart/storage.py:344)
  - 同理加载另一类全部样本：[`violation_samples = self._load_samples_by_label(1, violation_count)`](ai_proxy/moderation/smart/storage.py:350)
- 风险说明：
  - 当 `pass_count/violation_count` 很大时，上述两行会把大量 `Sample(text=...)` 对象一次性常驻到 Python 堆里，训练过程中内存暴涨
  - 即使训练结束，Python/NumPy/scikit-learn 的内存分配器也可能不把已释放内存归还 OS（arena 复用/碎片化），导致 RSS 长期高位并逐步被内核换出（出现 swap 很高的现象）
- 备注：
  - 这不依赖“不可信上游/协议异常”；只要数据库样本规模够大、且训练周期运行，就可能在生产长跑中稳定复现

3) **流式预读异常路径 stringify buffer 仍是高危“内存放大”点，但在“上游可信 + 下游超时断开”前提下优先级可下调为 DoS/异常风暴场景**
- 关键证据：
  - 缓冲累加：[`buffer.append(chunk)`](ai_proxy/proxy/upstream.py:142)
  - 验证失败时异常包含全量 buffer repr：[`debug:{buffer.__repr__()}`](ai_proxy/proxy/upstream.py:176)
- 备注：
  - 代码存在 1KB “保护性限制”以避免无限预读：[`current_size > 1048`](ai_proxy/proxy/upstream.py:154)，因此它不是严格意义的“无界增长”

### P2（条件触发：多进程/重复启动时可能造成后台任务叠加）
4) **后台永久循环任务可能在某些部署模式下叠加**
- 关键证据：
  - 调度器创建任务：[`asyncio.create_task(scheduler_loop(...))`](ai_proxy/moderation/smart/scheduler.py:149)，循环：[`while True`](ai_proxy/moderation/smart/scheduler.py:137)
  - 内存守护任务：[`asyncio.create_task(memory_guard_loop())`](ai_proxy/app.py:70)，循环：[`while True`](ai_proxy/app.py:75)
  - 防重复标志仅为进程内全局变量：[`_scheduler_started = False`](ai_proxy/app.py:39)
- 风险说明：
  - `uvicorn --workers N` 会创建 N 个进程，每个进程都会独立启动调度器与守护任务（预期行为但资源翻倍）
  - `reload` 或异常热重载情况下，若 startup 钩子被多次触发且标志失效，可能出现多个任务并存（表现为持续资源增长）

---

## 详细发现与复现思路

### 发现 1（P0）：SSEBufferTransformer 无界缓冲

**代码路径**
- 入口：[`create_stream_transformer()`](ai_proxy/proxy/stream_transformer.py:292) 返回 [`SSEBufferTransformer`](ai_proxy/proxy/stream_transformer.py:39)
- 核心逻辑：[`SSEBufferTransformer.feed()`](ai_proxy/proxy/stream_transformer.py:46)

**问题机制**
- `feed()` 把每次收到的 chunk decode 成 `text`，并无条件拼到 `self.buffer`：[`self.buffer += text`](ai_proxy/proxy/stream_transformer.py:52)
- 只有当 `self.buffer` 中出现 `\n\n`（SSE 事件分隔符）时才进行 split 并缩短 buffer：[`raw_event, self.buffer = self.buffer.split("\n\n", 1)`](ai_proxy/proxy/stream_transformer.py:56)
- 因此当输入流不包含 `\n\n` 时，`while` 循环一次都不会进入，buffer 永不缩短

**最小复现思路（概念）**
- 构造一个上游流持续发送 `b"data: {....}\\n"`，但永远不发送额外的 `\\n` 形成 `\\n\\n`
- 或发送任意持续增长字节流但没有 `\\n\\n`
- 调用 [`SSEBufferTransformer.feed()`](ai_proxy/proxy/stream_transformer.py:46) 反复喂入，即可观察 `self.buffer` 无限增长

**影响**
- 对长连接 streaming（SSE）路径，任意协议异常都可能拖垮整个服务进程

---

### 发现 2（P1）：Upstream 预读缓冲在异常路径 stringify 全量 buffer

**代码路径**
- 入口：[`UpstreamClient.forward_request()`](ai_proxy/proxy/upstream.py:50) 且 `delay_stream_header=True`
- 预读循环：[`while True`](ai_proxy/proxy/upstream.py:129)

**问题机制**
- 预读阶段把每个 chunk 存入列表 buffer：[`buffer.append(chunk)`](ai_proxy/proxy/upstream.py:142)
- 若一直不满足校验条件并最终结束，抛异常时将 `buffer.__repr__()` 拼接到异常字符串：[`debug:{buffer.__repr__()}`](ai_proxy/proxy/upstream.py:176)
- 同时 debug 输出会打印 chunk 文本内容前 500 字符：[`print(f"[UPSTREAM] Chunk content: {chunk_text[:500]}")`](ai_proxy/proxy/upstream.py:138)

**影响**
- 在“异常流/空回复/格式不满足”风暴下：
  - 每个失败请求都会产生额外字符串分配与日志 IO
  - 并发越高越容易出现内存尖峰、GC 抖动、看似“内存不回落”

---

## 其它模块审计结论（包含“模型训练/加载”相关）

1) 内存守护模块本身不构成泄漏：
- 使用 weakref 追踪容器：[`self._tracked_objects[obj_id] = (weakref.ref(container), name)`](ai_proxy/utils/memory_guard.py:143)
- 会清理失效引用：[`if container is None: to_remove.append(obj_id)`](ai_proxy/utils/memory_guard.py:162)

2) SQLite 连接池与缓存总体可控：
- 池有最大连接数：[`max_connections`](ai_proxy/moderation/smart/storage.py:24)，回收策略：[`if len(self._pool) < self.max_connections: self._pool.append(conn)`](ai_proxy/moderation/smart/storage.py:87)
- 全局池按 db_path 缓存：[`_connection_pools`](ai_proxy/moderation/smart/storage.py:101)，并支持 shutdown 清理：[`cleanup_pools()`](ai_proxy/moderation/smart/storage.py:113)（在 [`shutdown_event()`](ai_proxy/app.py:93) 调用：[`cleanup_pools()`](ai_proxy/app.py:120)）

3) AI 审核缓存有上限（可控）：
- profile 缓存字典上限：[`MAX_PROFILES = 50`](ai_proxy/moderation/smart/ai.py:68)
- 每 profile LRU 上限：[`CACHE_SIZE = 20`](ai_proxy/moderation/smart/ai.py:67)

4) 模型训练/模型加载 **不是“保证不泄漏”**：存在“全局字典键集合无界增长”的风险（取决于 profile 是否会持续新增）
- BoW 模型缓存（无上限淘汰）：[`_model_cache`](ai_proxy/moderation/smart/bow.py:28)
  - 只在“同名 profile 且模型文件 mtime 变化”时才删除旧值：[`del _model_cache[profile_name]`](ai_proxy/moderation/smart/bow.py:247)
  - 若服务长跑过程中 profile 名称持续新增（多租户/动态配置），该 dict 的 key 集合会持续增长，模型对象会常驻内存，表现为持续内存增长（长期运行“必出事”，直到 profile 停止增长为止）
- fastText 模型缓存（无上限淘汰）：[`_fasttext_cache`](ai_proxy/moderation/smart/fasttext_model.py:19)
  - 同样只在文件更新时删除旧值：[`del _fasttext_cache[profile_name]`](ai_proxy/moderation/smart/fasttext_model.py:164)
- 训练调度锁表（无上限淘汰）：[`_profile_locks`](ai_proxy/moderation/smart/scheduler.py:15)
  - 新 profile 首次训练就会插入锁对象：[`_profile_locks[profile_name] = asyncio.Lock()`](ai_proxy/moderation/smart/scheduler.py:21)
  - 若 profile 持续新增，锁表会持续增长（对象虽小，但属于“永久增长结构”，会造成进程内存基线逐渐升高）

结论边界（关键澄清）：
- 如果你的部署模型是“profile 集合固定”（例如仅 `default` 等少量配置），上述缓存/锁表增长是有界的，严格意义上不构成无限泄漏。
- 如果你的部署模型是“profile 可由请求驱动不断新增”，则上述结构将成为确定性的长期增长点，应当加入上限/TTL/LRU 或在不再使用时主动清理。

---

## 修复建议（按优先级）

### 必修（P0）
1) 为 [`SSEBufferTransformer`](ai_proxy/proxy/stream_transformer.py:39) 增加缓冲上限与丢弃策略
- 建议：
  - 增加 `MAX_BUFFER_CHARS` 或 `MAX_BUFFER_BYTES`
  - 超限后：要么截断为最后 N 字符；要么直接丢弃并输出 error SSE；要么中止流并关闭连接
  - 同时建议统计/监控超限事件，便于发现上游协议异常

### 强烈建议（P1）
2) 优化训练数据加载，避免“欠采样先全量加载”
- 建议方向（核心目标：把峰值从 O(N) 降到 O(max_samples)）：
  - 用 SQL 直接按标签随机抽样/按时间抽样，而不是先把 `pass_count/violation_count` 全部加载进 Python
  - 或先只取 ID（限制数量）再批量按 ID 查询文本，避免构造巨大 `Sample` 列表
  - 训练期间严格控制 `max_samples` 与 `max_db_items`，避免数据库规模无限增长

3) 禁止在异常中拼接全量 `buffer.__repr__()`，改为“摘要化”日志
- 建议：
  - 仅记录 `chunk_count`、`total_bytes`、首/尾 chunk 的前 N 字节摘要
  - 绝不 stringify 全量 bytes 列表

4) 降低 streaming 路径 debug 打印量
- 建议：
  - 将 chunk 内容打印改为可配置级别（debug 开关），生产默认关闭
  - 避免打印大对象（例如 [`print(f"... {data}")`](ai_proxy/proxy/stream_checker.py:178)）

### 可选（P2）
5) 明确部署策略下的后台任务行为
- 建议：
  - 文档明确：多 worker 会启动多份调度器
  - 如需全局唯一调度器：使用外部定时系统（cron/队列）或 leader 选举/分布式锁

### 建议新增（P2）
6) 为“模型缓存/锁表”增加上限或回收策略（当 profile 可能增长时）
- 建议方向：
  - 给 [`_model_cache`](ai_proxy/moderation/smart/bow.py:28) / [`_fasttext_cache`](ai_proxy/moderation/smart/fasttext_model.py:19) 增加最大 profile 数或 TTL/LRU
  - 给 [`_profile_locks`](ai_proxy/moderation/smart/scheduler.py:15) 增加定期清理：长期未出现的 profile 锁可删除

---

## 附录：引用清单（核心证据）
- [`SSEBufferTransformer.feed()`](ai_proxy/proxy/stream_transformer.py:46)
- [`self.buffer += text`](ai_proxy/proxy/stream_transformer.py:52)
- [`while "\n\n" in self.buffer`](ai_proxy/proxy/stream_transformer.py:55)
- [`await asyncio.to_thread(train_local_model, profile)`](ai_proxy/moderation/smart/scheduler.py:122)
- [`train_bow_model()`](ai_proxy/moderation/smart/bow.py:111)
- [`pass_samples = self._load_samples_by_label(0, pass_count)`](ai_proxy/moderation/smart/storage.py:344)
- [`violation_samples = self._load_samples_by_label(1, violation_count)`](ai_proxy/moderation/smart/storage.py:350)
- [`buffer.append(chunk)`](ai_proxy/proxy/upstream.py:142)
- [`debug:{buffer.__repr__()}`](ai_proxy/proxy/upstream.py:176)
- [`asyncio.create_task(scheduler_loop(...))`](ai_proxy/moderation/smart/scheduler.py:149)
- [`asyncio.create_task(memory_guard_loop())`](ai_proxy/app.py:70)
- [`_model_cache`](ai_proxy/moderation/smart/bow.py:28)
- [`del _model_cache[profile_name]`](ai_proxy/moderation/smart/bow.py:247)
- [`_fasttext_cache`](ai_proxy/moderation/smart/fasttext_model.py:19)
- [`del _fasttext_cache[profile_name]`](ai_proxy/moderation/smart/fasttext_model.py:164)
- [`_profile_locks`](ai_proxy/moderation/smart/scheduler.py:15)
- [`_profile_locks[profile_name] = asyncio.Lock()`](ai_proxy/moderation/smart/scheduler.py:21)