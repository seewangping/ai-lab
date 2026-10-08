# Python 语法速查（C# 开发者视角）

> 配套可运行版本：`python scripts/syntax_tour.py`（13 节，每节都有可跑代码 + 打印证据）
>
> 这份文档解决的不是"Python 有哪些语法"，而是**"我看到一行看不懂的 Python，该怎么办"**。

---

## 0. 读码五步法（卡住时按顺序执行）

| 步骤    | 动作                                                                        | 为什么                          |
| ----- | ------------------------------------------------------------------------- | ---------------------------- |
| ① 画骨架 | 先只看**缩进和冒号**，忽略所有内容，画出层级                                                  | Python 的块边界只由缩进决定，骨架不清就永远读不懂 |
| ② 找块头 | 行尾有 `:` 的行 = 块的开头（`if/for/while/def/class/try/with/match`）                | 没有 `{}`，冒号是唯一的块信号            |
| ③ 认符号 | `()` = 调用 / 元组 / 生成器表达式；`[]` = 索引 / 列表 / 推导式；`{}` = 字典 / 集合 / f-string 占位 | 三种括号承担了全部结构信息                |
| ④ 切长行 | 一行太长时，在最外层的**逗号**或**关键字**处切段，逐段看                                          | 长行不是"难"，是"没切开"               |
| ⑤ 改写  | **把复杂写法改写成最笨的中间变量版**                                                      | 这是性价比最高的一招，见下                |

### 第 ⑤ 步实战：一行改写成五行

```python
# 原文（streaming.py:126）
text = reply if reply is not None else build_reply(payload.model_copy(update={"stream": True}), settings)

# 改写：先切成三段，再逐段展开
if reply is not None:                      # ← 三元表达式展开
    text = reply
else:
    payload2 = payload.model_copy(update={"stream": True})   # ← 拆开嵌套调用
    text = build_reply(payload2, settings)
```

能改写出来 = 真读懂了。改不出来 = 卡在哪一段已经定位到了，去查那一段就行。

---

## 1. 块结构：花括号换成了缩进

```csharp
// C#
if (x > 2) { DoA(); DoB(); } else { DoC(); }
```

```python
# Python
if x > 2:            # ← 没有圆括号；行尾必须有冒号
    do_a()           # ← 缩进 4 空格 = 进入块
    do_b()           # ← 同缩进 = 同一块
else:
    do_c()
```

| 场景    | C#              | Python                       |
| ----- | --------------- | ---------------------------- |
| 空块    | `if (x) { }`    | `if x: pass` ← 必须写点什么        |
| 单行块   | `if (x) DoA();` | `if x: do_a()` ← 只含**第一条**语句 |
| 多语句续行 | 无所谓             | 靠**括号**自动续行；反斜杠 `\` 是下策      |
| 缩进风格  | 自由              | 4 空格，项目统一，**不能混 Tab**        |

**唯一高频事故**：`.py` 文件被别的编辑器用 Tab 改过 → `IndentationError`。修法是全文件统一成空格。

---

## 2. 类型标注对照表

Python 的标注**运行时真的存在**（函数上有 `__annotations__` 字典），但**不强制检查**——检查靠 mypy。

| Python                           | C#                                   | 备注                           |
| -------------------------------- | ------------------------------------ | ---------------------------- |
| `int` / `str` / `bool` / `float` | `int` / `string` / `bool` / `double` | `float` 是 64 位               |
| `str \| None`                    | `string?`                            | 3.10+ 写法，比 `Optional[str]` 短 |
| `list[int]`                      | `List<int>`                          | 3.9+ 可直接用小写                  |
| `dict[str, int]`                 | `Dictionary<string, int>`            |                              |
| `set[int]`                       | `HashSet<int>`                       |                              |
| `tuple[str, ...]`                | `IReadOnlyList<string>`              | 定长的写 `tuple[str, str]`       |
| `Callable[[int], str]`           | `Func<int, string>`                  |                              |
| `Awaitable[bool]`                | `Task<bool>`                         |                              |
| `Callable[[], Awaitable[bool]]`  | `Func<Task<bool>>`                   |                              |
| `Iterator[str]`                  | `IEnumerator<string>`                |                              |
| `AsyncIterator[str]`             | `IAsyncEnumerator<string>`           |                              |
| `type[AppError]`                 | `Type<AppError>`                     | 传的是**类本身**不是实例               |
| `Any`                            | `dynamic` / `object`                 | 尽量别用，用了 mypy 就不管了            |
| `Final = 3`                      | `const` / `readonly`                 | 只拦 mypy，运行时仍可改               |
| `Annotated[T, 元数据]`              | `[Attribute]`                        | 区别：它进**类型系统**，框架能取到元数据       |

`Annotated` 是理解本项目 `deps.py` 的钥匙：

```python
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
#                 ↑类型      ↑元数据：告诉 FastAPI「这个参数怎么造」
```

---

## 3. 函数签名解剖

```python
def f(a, /, b, *args, c, d=1, **kwargs) -> str:
    ...
```

| 部分           | 含义              | C# 近似             |
| ------------ | --------------- | ----------------- |
| `a` 在 `/` 左边 | 只能按**位置**传      | 普通参数              |
| `/`          | 位置参数与普通参数的分界    | 无                 |
| `b`          | 位置/关键字都行        | 普通参数              |
| `*args`      | 多余的位置参数 → tuple | `params object[]` |
| `*` 单独出现     | 后面的只能按**关键字**传  | 无强制机制             |
| `c`          | 必须写 `c=...`     | 命名参数（但 C# 不强制）    |
| `d=1`        | 有默认值 → 可选       | 可选参数              |
| `**kwargs`   | 多余的关键字参数 → dict | 无                 |
| `-> str`     | 返回类型            | `: string` 在签名前   |

**本项目的惯例**：只要参数多了，就在第一个参数后加 `*`，例如

```python
def sse_frame(data: str, *, event=None, event_id=None, retry_ms=None) -> str:
```

好处：以后加参数不会破坏已有调用。

### 唯一必踩的坑：可变默认值

```python
def bad(item, target=[]):     # 默认值只在【定义时】求值一次，所有调用共享同一个 list
    target.append(item)
    return target

bad("a")   # ['a']
bad("b")   # ['a', 'b']  ← 上次的结果还在！
```

C# 反而不会踩：它的可选参数值必须是编译期常量，引用类型只能写 `= null`。**Python 放开了限制，陷阱就来了**。修法固定写成 `target=None` + 函数体内 `if target is None: target = []`。

---

## 4. 高频语法糖对照表

| 目的      | C#                      | Python                          |
| ------- | ----------------------- | ------------------------------- |
| 字符串插值   | `$"hi {name}"`          | `f"hi {name}"`                  |
| 三元      | `c ? x : y`             | `x if c else y`（结果在前）           |
| 空值兜底    | `a ?? b`                | `a if a is not None else b`     |
| 字典取值兜底  | `TryGetValue`           | `d.get(k, 默认值)`                 |
| 交换两个变量  | `(a, b) = (b, a)`       | `a, b = b, a`                   |
| 反转序列    | `Enumerable.Reverse()`  | `xs[::-1]`                      |
| 切片      | `xs[1..5]`              | `xs[1:5]`                       |
| 取末尾     | `xs[^1]`                | `xs[-1]`                        |
| 遍历带下标   | `for (var i = 0; ...)`  | `for i, x in enumerate(xs):`    |
| 遍历字典    | `foreach (var kv in d)` | `for k, v in d.items():`        |
| 拼接可迭代对象 | `Concat`                | `xs.extend(ys)` / `xs + ys`     |
| 过滤+投影   | `.Where().Select()`     | `[f(x) for x in xs if p(x)]`    |
| 集合去重    | `Distinct()`            | `set(xs)`                       |
| 排序      | `.OrderBy(x => x.K)`    | `sorted(xs, key=lambda x: x.k)` |
| 抛异常     | `throw new X("m")`      | `raise X("m")`                  |
| 捕获      | `catch (IOException e)` | `except IOError as e:`          |
| finally | `finally { }`           | `finally:`                      |
| using   | `using (var f = ...)`   | `with open(...) as f:`          |
| 静态类方法   | `static`                | `@staticmethod`                 |
| 只读属性    | `public int V => _v;`   | `@property`                     |
| 抽象方法    | `abstract`              | `raise NotImplementedError`     |
| 接口      | `interface`             | `Protocol` / ABC（鸭子类型为主）        |
| 常量      | `const int N = 3;`      | `N: Final = 3`                  |
| 命名空间    | `namespace A.B`         | 目录结构 + `__init__.py`            |
| 入口      | `static void Main`      | `if __name__ == "__main__":`    |
| 并发等待全部  | `Task.WhenAll`          | `asyncio.gather`                |
| 异步遍历    | `await foreach`         | `async for`                     |

**没有对应物的三个**：`yield` 之外的生成器表达式、`:=` 海象运算符、`/` 与 `*` 的位置/关键字强制。

---

## 5. 六个「读代码路障」及解码法

### A. 装饰器 `@xxx`

读作：**下面这个函数被上面那层包了一下**。

```python
@app.get("/health")          # ← 带参数的装饰器，多一层
def health() -> dict: ...

# 完全等价于：
def health() -> dict: ...
health = app.get("/health")(health)
```

多层时：**从下往上包**。

```python
@A
@B
def f(): ...        # f = A(B(f))
```

### B. 推导式 `[ ... for ... if ... ]`

固定三段，按**这个顺序**读：

```
[ ③表达式  for ①变量 in ②可迭代  if ④条件 ]
```

- ① 数据从哪来（`for`）
- ② 筛掉哪些（`if`，可多个 = `and`）
- ③ 每个变成什么（最前面的表达式）

```python
f"{r}{c}" for r in "AB" for c in "12"     # 两个 for = 嵌套循环，左边是外层
{n % 3 for n in xs}                        # 花括号无冒号 → set，自动去重
(n * n for n in xs)                        # 圆括号 → 生成器，惰性、不占内存
```

### C. 链式调用与属性

区分**有括号**和**没括号**：

```python
payload.model_copy(update={"stream": True})
#  ①②      ③        ④
# ① 变量 ② 属性/方法名（没括号，只是"取值"）③ 括号 = 调用 ④ 关键字参数
```

`request.is_disconnected` 后面没括号 → 它是**函数对象本身**（当参数传给别人的），  
`request.is_disconnected()` 有括号 → 是**调用它拿结果**。这一字之差在 `api.py:54` 和 `streaming.py:143` 是刻意区分的。

### D. 嵌套调用做参数

括号一多就晕。解法：**从最里层往外读**，或按 `(` 缩进对齐。

```python
build_reply(payload.model_copy(update={"stream": True}), settings)
#  ①          ②                                      ③
# ① 最终要调用的函数  ② 它的第一个参数（本身又是一次调用）  ③ 第二个参数
```

### E. `async` / `await` 交错

- `async def` = 定义一个**协程函数**。调用它**不执行任何代码**，只造出一个协程对象。
- `await x` = **在这里等** x 完成；等待期间把控制权交还给事件循环（别的协程趁机跑）。
- 一行里出现多个 `await`，就是**多个等待点**，不改变执行顺序。

```python
if is_disconnected is not None and await is_disconnected():
#                              ↑ 在这里等一个"可能返回 bool 的可等待对象"
```

### F. 类型标注里的符号

| 符号                    | 读作                                                |
| --------------------- | ------------------------------------------------- |
| `A \| B`              | "A 或者 B"                                          |
| `list[X]`             | "一堆 X"                                            |
| `dict[K, V]`          | "K 到 V 的映射"                                       |
| `Callable[[A, B], C]` | "吃 A 和 B、吐 C 的函数"                                 |
| `X \| None = None`    | "可以不给，那么它是 None" ← **必须写成 `= None` 才能让框架走"缺省"分支** |

最后一条是 `deps.py` 里 401 而不是 422 的关键：如果写成 `x_api_key: str = Header(...)`（不给默认值），FastAPI 会在缺头时报 422；写成 `str | None = None`，才轮到我们自己抛 401。

---

## 6. 真实代码逐行解码

### 例 1 · `config.py:16`

```python
_BASE_DIR = Path(__file__).resolve().parents[2]
```

| 片段            | 读作                               |
| ------------- | -------------------------------- |
| `__file__`    | 当前文件的路径（字符串）                     |
| `Path(...)`   | 包成路径对象                           |
| `.resolve()`  | 转绝对路径，消掉 `..` 和软链                |
| `.parents[2]` | 往上第 3 层：`ai_lab/` → `src/` → 项目根 |

### 例 2 · `streaming.py:70`

```python
lines.extend(f"data: {line}" for line in data.split("\n"))
```

| 片段                                | 读作                    |
| --------------------------------- | --------------------- |
| `data.split("\n")`                | 按换行切成 list            |
| `f"data: {line}" for line in ...` | 生成器表达式：给每行加前缀         |
| `lines.extend(...)`               | 把生成器里的东西逐个追加进 `lines` |

等价于：`for line in data.split("\n"): lines.append(f"data: {line}")`

### 例 3 · `deps.py:57`

```python
ApiKeyHeaderDep = Annotated[str | None, Depends(_api_key_scheme)]
```

拆成 3 部分：

1. `ApiKeyHeaderDep` —— 这只是个**变量名**，等价于 C# 里 `using ApiKeyHeaderDep = ...;` 的类型别名。`xxxDep` 是本项目的约定：凡是 `Annotated[..., Depends(...)]` 都叫 `XxxDep`
2. `str | None` —— 值的类型：可能拿到字符串，也可能什么都没有（None）。这就是 Python 3.10+ 的联合类型写法，等于 C# 的 `string?`
3. `Annotated[类型, 元数据]` —— **类型不变，只是挂上一句"这个值从哪来"**。`Depends(_api_key_scheme)` 就是那句元数据：FastAPI 读到它才知道要去请求头里找 `X-API-Key`

关键认知：`Annotated` 本身在运行时几乎不做事，它纯粹是给框架读的"说明书"。
没有 FastAPI，`ApiKeyHeaderDep` 就只是 `str | None` 的别名。

**同一个位置，换一个元数据就换一个取值来源**——对比着看：

```python
# 直接声明成普通请求头参数
x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None

# 声明成"安全方案"（本项目的写法）
x_api_key: Annotated[str | None, Depends(_api_key_scheme)]
```

第一行有 `= None`、第二行没有——这个差别不是风格问题：
`Depends` 本身会提供值，所以不能再给默认值；而第一行的 `= None` 正是让它被
OpenAPI 记成"选填参数"的原因（详见 README 的「鉴权声明的两种写法」）。

顺带一个读码技巧：**看到 `Annotated` 里带着 `Depends`，就去文件上方找那个 `_xxx_scheme`
变量的定义**——参数上只有结果，真正"从哪取"写在变量定义处。

### 例 4 · `streaming.py:142-157`（最像"C# 里没有的东西"的一段）

```python
async for token in tokenize(text, delay_s=delay_s):
    if is_disconnected is not None and await is_disconnected():
        return
    sent += 1
    yield sse_json_frame({"text": token, "index": sent}, event="token")
    if sent % heartbeat_every == 0:
        yield sse_comment(f"ka-{sent}")
```

逐行：

1. `async for` = 从一个**异步生成器**里逐个取（取的过程可能要 await）
2. 每次取到 token，先问"客户端还在吗"，不在就 `return` 结束整个流
3. `sent += 1` 计数
4. `yield` = **把这一帧吐出去，然后暂停在这里**，等调用方来要下一帧
5. 每 20 个 token 插一条心跳注释帧

★ 理解 `yield` 的关键：这个函数**不是一次性算完返回**，而是"要一帧给一帧"。  
`return` 表示流结束（连 `done` 帧都不发了）；函数自然走到末尾也会结束流。

---

## 7. 报错 → 原因速查

| 报错                                                                       | 通常原因                             | 对应 C# 直觉                     |
| ------------------------------------------------------------------------ | -------------------------------- | ---------------------------- |
| `IndentationError`                                                       | Tab/空格混用，或缩进层级不连续                | 不会有（C# 忽略缩进）                 |
| `NameError: name 'x' is not defined`                                     | 用了**还没执行到**的定义                   | 编译错误 CS0103，但 Python 是运行时才发现 |
| `AttributeError`                                                         | 访问了不存在的属性 / 对象是 `None`           | `NullReferenceException`     |
| `TypeError: f() takes 2 positional arguments but 3 were given`           | 参数个数不对                           | 编译错误                         |
| `ModuleNotFoundError`                                                    | 路径 / 虚拟环境不对                      | 引用找不到程序集                     |
| `RuntimeWarning: coroutine 'f' was never awaited`                        | 调了 `async def` 但没 `await`（静默不执行） | 不会出现，C# 会编译错                 |
| `RuntimeError: asyncio.run() cannot be called from a running event loop` | 在协程里又开了一个 loop                   | 不会出现                         |
| `pydantic.ValidationError`                                               | 入参不满足 `Field()` 约束               | `ArgumentException`          |

---

## 8. 自测（能答上就算过关）

1. `if x:` 和 `if x is not None:` 什么时候结果不同？举一个具体值。[ ]
2. `(n for n in range(3))` 的类型是什么？`list()` 它两次，第二次得到什么？list<int>  list<list<int>>
3. 类体里的 `registry: list[int] = []`，和 `__init__` 里的 `self.registry = []`，行为差在哪？
4. `@app.get("/health")` 在 `def health()` 前后，实际发生了哪两次调用？
5. `def f(a, /, b, *, c)` 里，`f(1, 2, 3)` 会怎样？为什么本项目爱用 *`,`？param a 只能用位置传参，c只能用参数名传参，b都可以。使用* 可以在增加参数是不影响以前的调用*
6. `async def f(): ...` 调用 `f()` 之后，函数体执行了几行？

<details>

<summary>答案</summary>

1. `x = []` 时：`if x:` 为假（空容器是假值），`if x is not None:` 为真。
2. `generator`；第二次得到 `[]`——生成器只能走一遍，耗尽后就空了。
3. 前者是**类属性**，所有实例共用同一个 list（≈ C# static 字段）；后者是**实例属性**，每个实例独立。前者是经典事故来源。
4. ① 先调 `app.get("/health")` 拿到一个装饰器函数；② 再用它包 `health`，并把返回值赋回名字 `health`。
5. 报 `TypeError`——`c` 只能按关键字传，必须写 `f(1, 2, c=3)`。用 `*,` 是为了强制关键字传参，这样以后加参数不会破坏已有调用。
6. **零行**。只造出一个协程对象；必须 `await` 或交给事件循环才会执行。

</details>
