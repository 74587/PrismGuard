# 内存分析指南

## 项目内存问题总结

根据 `MEMORY_LEAK_REPORT.md` 的审计结果，当前项目存在以下主要内存问题：

### P0 级别（确定性内存泄漏）

1. **SSE 缓冲区无界增长** (`ai_proxy/proxy/stream_transformer.py`)
   - 问题：`SSEBufferTransformer.buffer` 在缺少 `\n\n` 分隔符时会无限增长
   - 触发条件：上游返回异常流、恶意攻击、协议不兼容
   - 影响：长时间运行必然 OOM

### P1 级别（高危内存峰值）

2. **BoW 训练全量加载样本** (`ai_proxy/moderation/smart/bow.py`)
   - 问题：欠采样策略会一次性加载全部样本到内存
   - 影响：样本量大时产生巨大内存峰值，RSS 不回落

3. **流式预读异常路径** (`ai_proxy/proxy/upstream.py`)
   - 问题：异常时 stringify 全量 buffer
   - 影响：异常风暴下内存尖峰、GC 抖动

4. **模型缓存无上限** (`ai_proxy/moderation/smart/bow.py`, `fasttext_model.py`)
   - 问题：`_model_cache` 和 `_fasttext_cache` 无 LRU/TTL
   - 影响：profile 持续新增时内存基线逐渐升高

### P2 级别（条件触发）

5. **后台任务叠加** (`ai_proxy/app.py`)
   - 问题：多 worker 或热重载时可能重复启动任务
   - 影响：资源翻倍消耗

---

## 内存分析工具

### 1. 基础内存分析器 (`tools/memory_profiler.py`)

**功能：**
- 进程级内存监控（RSS、VMS、占用率）
- Python 对象类型分布分析
- 容器统计（dict、list、set）
- 大对象检测（>1MB）
- GC 统计信息

**使用方法：**

```bash
# 生成当前进程内存报告
python tools/memory_profiler.py --report

# 持续监控当前进程（5秒间隔，10分钟）
python tools/memory_profiler.py --monitor --interval 5 --duration 10

# 附加到指定进程进行监控
python tools/memory_profiler.py --attach <PID> --interval 5
```

**适用场景：**
- 快速了解进程内存使用情况
- 监控内存增长趋势
- 识别大对象和容器

---

### 2. 高级内存追踪器 (`tools/advanced_memory_tracker.py`)

**功能：**
- 使用 `tracemalloc` 追踪内存分配
- 快照对比（查看内存增长来源）
- 带回溯的内存分配分析
- 使用 `objgraph` 分析对象引用
- 内存泄漏检测（循环引用、无法回收对象）

**使用方法：**

```bash
# 交互式内存分析（推荐）
python tools/advanced_memory_tracker.py --interactive

# 使用 objgraph 分析对象
python tools/advanced_memory_tracker.py --objgraph

# 检测内存泄漏
python tools/advanced_memory_tracker.py --leaks
```

**交互式命令：**
```
snapshot [label]      - 拍摄快照
compare [idx1] [idx2] - 比较快照（默认：0 和 -1）
top [n]               - 显示 Top N 内存分配
traceback [n]         - 显示带回溯的 Top N
current               - 显示当前内存使用
objgraph              - 使用 objgraph 分析
leaks                 - 检测内存泄漏
gc                    - 执行垃圾回收
list                  - 列出所有快照
quit                  - 退出
```

**适用场景：**
- 深入分析内存分配来源
- 对比不同时间点的内存变化
- 查找内存泄漏和循环引用

---

### 3. 运行时内存监控注入 (`tools/inject_memory_monitor.py`)

**功能：**
- 在运行中的 FastAPI 应用中注入内存监控端点
- 通过 HTTP API 实时查询内存状态
- 支持触发 GC、拍摄快照、清空缓存等操作

**集成方法：**

在 `ai_proxy/app.py` 中添加：

```python
from tools.inject_memory_monitor import inject_memory_endpoints

# 在创建 app 后调用
inject_memory_endpoints(app)
```

**可用端点：**

```bash
# 内存状态概览
curl http://localhost:8000/_debug/memory/status

# Top N 内存分配
curl http://localhost:8000/_debug/memory/top?limit=20

# 对象类型分布
curl http://localhost:8000/_debug/memory/objects?limit=20

# 容器统计
curl http://localhost:8000/_debug/memory/containers

# 应用缓存状态
curl http://localhost:8000/_debug/memory/caches

# 内存泄漏检测
curl http://localhost:8000/_debug/memory/leaks

# 触发垃圾回收
curl -X POST http://localhost:8000/_debug/memory/gc

# 拍摄内存快照
curl -X POST http://localhost:8000/_debug/memory/snapshot

# 清空应用缓存
curl -X POST http://localhost:8000/_debug/memory/clear_caches
```

**适用场景：**
- 生产环境实时监控
- 远程诊断内存问题
- 定期清理缓存和触发 GC

---

## 推荐的内存分析流程

### 场景 1：发现内存持续增长

1. **使用基础监控器观察趋势**
   ```bash
   python tools/memory_profiler.py --attach <PID> --interval 10
   ```

2. **生成内存报告，识别大对象**
   ```bash
   python tools/memory_profiler.py --report
   ```

3. **使用高级追踪器定位增长源**
   ```bash
   python tools/advanced_memory_tracker.py --interactive
   
   # 在交互模式中：
   >>> snapshot baseline
   # 等待一段时间或执行操作
   >>> snapshot after_operation
   >>> compare 0 1
   >>> traceback 10
   ```

4. **检查应用缓存**
   ```bash
   curl http://localhost:8000/_debug/memory/caches
   ```

### 场景 2：内存峰值过高

1. **检查容器和大对象**
   ```bash
   curl http://localhost:8000/_debug/memory/containers
   curl http://localhost:8000/_debug/memory/objects
   ```

2. **查看 Top 内存分配**
   ```bash
   curl http://localhost:8000/_debug/memory/top?limit=50
   ```

3. **使用 tracemalloc 追踪峰值时刻**
   ```bash
   python tools/advanced_memory_tracker.py --interactive
   
   # 在峰值前后拍摄快照对比
   ```

### 场景 3：怀疑内存泄漏

1. **检测循环引用和无法回收对象**
   ```bash
   curl http://localhost:8000/_debug/memory/leaks
   ```

2. **使用 objgraph 分析对象增长**
   ```bash
   python tools/advanced_memory_tracker.py --objgraph
   ```

3. **长时间监控对象数量变化**
   ```bash
   python tools/memory_profiler.py --monitor --interval 60 --duration 120
   ```

---

## 依赖安装

```bash
# 基础依赖（已在 requirements.txt 中）
pip install psutil

# 高级分析依赖（可选）
pip install objgraph
pip install graphviz  # 用于生成对象引用图
```

---

## 内存优化建议

### 立即修复（P0）

1. **为 SSEBufferTransformer 添加缓冲上限**
   ```python
   MAX_BUFFER_SIZE = 1024 * 1024  # 1MB
   if len(self.buffer) > MAX_BUFFER_SIZE:
       # 截断或抛出异常
   ```

### 强烈建议（P1）

2. **优化 BoW 训练数据加载**
   - 使用 SQL 直接抽样，避免全量加载
   - 限制 `max_samples` 和 `max_db_items`

3. **禁止异常中拼接全量 buffer**
   - 仅记录摘要信息（大小、数量、首尾片段）

4. **为模型缓存添加 LRU/TTL**
   ```python
   from functools import lru_cache
   
   @lru_cache(maxsize=10)
   def load_model(profile_name):
       ...
   ```

### 可选优化（P2）

5. **定期清理缓存**
   - 使用内存守护器定期检查和清理
   - 在低峰期触发 GC 和 malloc_trim

6. **监控和告警**
   - 集成 Prometheus/Grafana 监控内存指标
   - 设置内存阈值告警

---

## 常见问题

### Q: RSS 不下降，swap 持续增长？

**原因：** glibc 的内存分配器会保留已释放的内存在 arena 中

**解决：** 使用 `malloc_trim()` 强制归还内存给 OS

```python
from ai_proxy.utils.memory_guard import release_memory

# 在删除大对象后调用
del large_object
release_memory()  # 执行 GC + malloc_trim
```

### Q: 如何找到内存泄漏的具体位置？

**步骤：**
1. 使用 `advanced_memory_tracker.py` 的交互模式
2. 在操作前后拍摄快照
3. 使用 `compare` 命令查看增长
4. 使用 `traceback` 命令查看调用栈

### Q: 模型缓存占用太多内存？

**解决：**
```bash
# 清空模型缓存
curl -X POST http://localhost:8000/_debug/memory/clear_caches

# 或在代码中
from ai_proxy.moderation.smart.bow import _model_cache
_model_cache.clear()
```

### Q: 如何在生产环境监控内存？

**推荐方案：**
1. 集成 `inject_memory_monitor.py` 提供 HTTP 端点
2. 使用 Prometheus 采集 `/_debug/memory/status`
3. 配置 Grafana 仪表盘和告警规则
4. 定期调用 `/_debug/memory/gc` 清理内存

---

## 参考资料

- [Python tracemalloc 文档](https://docs.python.org/3/library/tracemalloc.html)
- [objgraph 文档](https://mg.pov.lt/objgraph/)
- [psutil 文档](https://psutil.readthedocs.io/)
- [内存泄漏审计报告](./MEMORY_LEAK_REPORT.md)
