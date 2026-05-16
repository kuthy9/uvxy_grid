# Cleanup Log (Phase 6 of production refactor)

**日期**: 2026-05-15

## Grep 结果

### Step 1: 伪代码 / hardcode 收益

```
无 hits.
```

**分类**:
- 假阳性: 0 处
- 真违规: 0 处

### Step 2: Wall-clock 违反 (datetime.now())

原始 grep 结果共 48 hits，分布如下：

**业务模块 (核心业务逻辑) 中的违规**:
```
grid_engine.py:88:        current_time = current_time or datetime.now()
grid_engine.py:197:        current_time = current_time or datetime.now()
grid_engine.py:233:        current_time = current_time or datetime.now()
```

3 处真违规 — 都是参数默认值的 fallback，违反 CLAUDE.md §9 规则"业务模块全部走 self.clock.now()"。

**非业务模块 (infrastructure / testing) 中的使用** (无违规):
- trade_logger.py (8 hits): 基础设施/日志层，不是核心业务逻辑，允许使用 datetime.now()
- report_generator.py (1 hit): 报告生成，不是核心业务逻辑，允许使用 datetime.now()
- data_provider.py (2 hits): 数据获取工具，基础设施，允许使用 datetime.now()
- test.py (27 hits): 单元测试，允许使用 datetime.now()

**结论**: 3 处真违规需要修复，均在 grid_engine.py。其余 45 hits 都在非业务模块中，符合规则。

### Step 3: ib_insync 隔离 (import / from ib_insync)

```
无 hits.
```

**结论**: ib_insync 隔离完整，只在 ibkr_executor.py 中出现，无外泄。符合 CLAUDE.md §9 规则。

---

## 修复列表

### 修复 1: grid_engine.py 第 88 行

**文件**: `/Users/krisjiang/Desktop/grid/grid_engine.py`

**原代码**:
```python
current_time = current_time or datetime.now()
```

**问题**: GridEngine 是核心业务模块，不应该有 wall-clock 的 fallback。所有时间应该由调用方显式传递。

**方案**: 移除 fallback，假设 current_time 始终非 None。在 GridEngine 初始化时，调用方（grid_bot）已经保证传递 `self.clock.now()`。

### 修复 2: grid_engine.py 第 197 行

**文件**: `/Users/krisjiang/Desktop/grid/grid_engine.py`

**原代码**:
```python
current_time = current_time or datetime.now()
```

**位置**: `should_recenter()` 方法中

**方案**: 同样移除 fallback，假设 current_time 参数始终由 grid_bot 显式传递。

### 修复 3: grid_engine.py 第 233 行

**文件**: `/Users/krisjiang/Desktop/grid/grid_engine.py`

**原代码**:
```python
current_time = current_time or datetime.now()
```

**位置**: `recenter()` 方法中

**方案**: 同样移除 fallback，假设 current_time 参数始终由 grid_bot 显式传递。

---

## 实施说明

- **3 处修复**: 改写 grid_engine.py，去掉 3 个 `datetime.now()` fallback
- **验证**: 运行 `python test.py` 确保所有单元测试通过
- **回归检查**: grid_bot 中的调用位置 (line 742, 953, 960) 均已显式传递 current_time，无需调整
- **影响范围**: 仅 grid_engine.py，没有其他文件需要改动
