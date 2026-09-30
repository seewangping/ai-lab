"""Python 语法导览 —— 写给「C# 大脑」的对照版。

为什么有这个文件：
  你写 C# 十几年，读 Python 时"每个单词都认识，但连起来不知道在干嘛"，
  根因不是词汇量，而是【块结构、求值时机、类型模型】这三套底层约定不一样。
  逐条背语法没用，要的是把"这行 C# 会怎么写"接到"这行 Python 在干嘛"上。

用法：
  python scripts/syntax_tour.py          # 跑全部 13 节
  python scripts/syntax_tour.py 4        # 只看第 4 节（推导式）
  python scripts/syntax_tour.py 4 8 11   # 看多节

每节结构固定：
  ① 标题 + C# 对照一句话
  ② 能跑的 Python 代码
  ③ 打印出来的结果 —— 这些输出就是"证据"，别只看代码

配套文档：docs/python-syntax-for-csharp-devs.md（速查表 + 读码五步法）
"""

import asyncio
import functools
import sys
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from typing import Annotated, Final, get_args

LINE = "=" * 74
CLI_SECTIONS: dict[int, str] = {}


def head(n: int, title: str, cs: str) -> None:
    print(f"\n{LINE}\n[{n:02d}] {title}\n     C# 对照：{cs}\n{LINE}")


def note(text: str) -> None:
    print(f"     {text}")


def register(n: int, title: str):
    """把节函数登记到 CLI_SECTIONS，顺便少写一遍编号。"""

    def deco(fn):
        CLI_SECTIONS[n] = f"{title}"
        return fn

    return deco


# ---------------------------------------------------------------- 01 缩进
@register(1, "缩进即语法：花括号去哪了")
def s01() -> None:
    head(1, "缩进即语法（没有花括号）", "C# 的 { } 换成「冒号 + 缩进」")

    x = 3
    if x > 2:
        print("   a. 我在 if 体内（缩进 4 空格）")
        print("   b. 我和 a 同缩进 = 同一个块")
    else:
        print("   c. 我在 else 体内")

    note("读法：看到行尾的冒号 : → 下一行缩进加深 → 进入新块；缩进退回来 → 块结束。")
    note("Python 没有『块结束符』，所以缩进错不是排版问题，是语法错误。")

    # 单行写法：冒号后只有【第一条】语句属于该块
    if x > 2:
        print("   d. 单行 if 里的第一条")
    print("   e. 这行和 if 无关，永远执行")

    # 空块不能留白，必须 pass
    if x < 0:
        pass  # C# 里写 {} 就行，Python 必须写点什么

    note("缩进混乱时怎么自救：把光标放行首，看前面是 Tab 还是空格。")
    note("本项目统一 4 空格（见 pyproject/ruff 配置），混用会 IndentationError。")


# ---------------------------------------------------------------- 02 类型标注
@register(2, "类型标注：它真的是类型，不是注释")
def s02() -> None:
    head(2, "类型标注（PEP 484）", "C# 的 string? / List<Dictionary<string,int>>")

    def anno_demo(
        uid: int,
        name: str | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, int]:
        return {"uid": uid}

    note("先证明它不是注释——运行时能把这个 dict 读出来：")
    for k, v in anno_demo.__annotations__.items():
        print(f"       {k:8} -> {v!r}")

    note("")
    note("对照表（左 = Python，右 = C#）：")
    for py, cs in [
        ("str | None", "string?"),
        ("Optional[str]", "string?（老写法，3.10 起用 | 更短）"),
        ("list[int]", "List<int>"),
        ("dict[str, int]", "Dictionary<string, int>"),
        ("tuple[str, ...]", "IReadOnlyList<string>（定长用 tuple[str, str]）"),
        ("set[int]", "HashSet<int>"),
        ("Callable[[], Awaitable[bool]]", "Func<Task<bool>>"),
        ("type[AppError]", "Type<AppError>（传类本身而非实例）"),
        ("Iterator[str]", "IEnumerator<string>"),
        ("Final = 常量", "const / readonly"),
    ]:
        print(f"       {py:28} {cs}")

    SECRET: Final = "abcdef"  # Final 只是给 mypy 看的，运行时仍可改
    note("")
    note(f"Final 常量拦的是 mypy，不是运行时：SECRET = {SECRET!r} 照样能重新赋值。")

    Alias = Annotated[int, "1~100 之间"]
    note("Annotated[T, 元数据] = 类型 T 挂上额外说明，框架靠它拿规则：")
    print(f"       get_args(Annotated[int, '1~100 之间']) -> {get_args(Alias)}")
    note("       第 2 项起就是元数据，FastAPI 靠它拿到 Depends(...) / Query(...) 这类信息。")
    note("       本项目 deps.py: Annotated[Settings, Depends(get_settings_dep)] 就是这个用法。")


# ---------------------------------------------------------------- 03 函数签名
@register(3, "函数签名解剖 + 默认值陷阱")
def s03() -> None:
    head(3, "函数签名解剖", "C# 的 ref/out/params/命名参数")

    def demo(pos_only, /, normal, *args, kw_only, **kwargs):
        return {
            "pos_only": pos_only,
            "normal": normal,
            "args(元组)": args,
            "kw_only": kw_only,
            "kwargs(字典)": kwargs,
        }

    note("签名里三个分隔符的含义：")
    note("  a, /, b   → / 左边的 a 只能按【位置】传")
    note("  a, *, b   → * 右边的 b 只能按【关键字】传")
    note("  *args     → 多余的【位置】参数打成 tuple")
    note("  **kwargs  → 多余的【关键字】参数打成 dict")
    print()
    note("本次调用传入：1, 2, 3, 4, kw_only=5, extra=6")
    print(f"       {demo(1, 2, 3, 4, kw_only=5, extra=6)}")
    note("")
    note("为什么本项目到处写 `*,`（如 sse_frame(data, *, event=None)）：")
    note("  强制调用方写关键字 → 加参数时不破坏已有调用，可读性也更高。")

    # ------- 默认值陷阱 -------
    note("")
    note("--- 默认值陷阱（C# 不会踩，Python 必踩）---")

    def bad(item, target=[]):  # 默认值在【函数定义时】求值一次，全程复用
        target.append(item)
        return target

    print(f"       bad('a') -> {bad('a')}")
    print(f"       bad('b') -> {bad('b')}   ← 上次的结果还在！")

    def good(item, target=None):
        if target is None:
            target = []
        target.append(item)
        return target

    print(f"       good('a') -> {good('a')}")
    print(f"       good('b') -> {good('b')}   ← 正确")
    note("记忆点：默认值只在【定义时】求值一次，之后被所有调用共享。")
    note("其实 C# 不会踩这个坑 —— 它的可选参数值必须是编译期常量，")
    note("引用类型只能写 = null，再用重载或 (x ?? new List<int>()) 兜底，限制反而保护了你。")
    note("Python 把限制放开了，陷阱也就跟着来了。")


# ---------------------------------------------------------------- 04 推导式
@register(4, "推导式：LINQ 的语法糖")
def s04() -> None:
    head(4, "推导式 / comprehension", "C# 的 xs.Where(...).Select(...)")

    xs = [1, 2, 3, 4, 5]

    note("[表达式 for 变量 in 可迭代 if 条件]  —— 三段固定，按顺序读：")
    note("   ① for  去哪拿数据    ② if  筛哪些    ③ 表达式  把每个变成什么")
    print()
    print(f"       [n * n for n in xs if n % 2]  -> {[n * n for n in xs if n % 2]}")
    note("       C#: xs.Where(n => n % 2 != 0).Select(n => n * n).ToList()")

    print(f"       {{n: n * n for n in xs}}          -> {{n: n * n for n in xs}}")
    note("       C#: xs.ToDictionary(n => n, n => n * n)")

    print(f"       {{n % 3 for n in xs}}             -> {{n % 3 for n in xs}}")
    note("       注意：外面是花括号但没有冒号 → 集合(set)推导式，自动去重")

    # 生成器表达式：圆括号 = 惰性，不占内存
    gen = (n * n for n in xs)
    note("")
    print(f"       (n * n for n in xs) 的类型 -> {type(gen).__name__}（不是 tuple！是生成器）")
    print(f"       取值 -> {list(gen)}")
    note("       圆括号推导式是『生成器表达式』：不立刻计算，边遍历边算。")

    # 嵌套：读法 = 从左到右就是外层到内层
    pairs = [f"{r}{c}" for r in "AB" for c in "12"]
    print(f"\n       [f'{{r}}{{c}}' for r in 'AB' for c in '12'] -> {pairs}")
    note("       两个 for 的顺序 = 嵌套 for 的顺序（左 = 外层），和 C# 的 from 子句一致")

    # 多重条件
    note("")
    print(f"       带两个 if -> {[n for n in xs if n > 1 if n < 5]}")
    note("       等价于 if n > 1 and n < 5（连续 if 就是 and）")

    # 本项目的真实用例
    note("")
    note("--- 你代码里的真实用例 ---")
    lines = ["event: token", 'data: {"text":"x"}']
    for i, ln in enumerate(lines):
        print(f"       streaming.py: lines.extend(f'data: {{line}}' for line in ...)  →  第{i + 1}行: {ln!r}")
    note("       那行 `lines.extend(f\"data: {line}\" for line in data.split(chr(10)))`")
    note("       拆开写 =  for line in data.split('\\n'): lines.append(f'data: {line}')")


# ---------------------------------------------------------------- 05 切片
@register(5, "切片与负索引")
def s05() -> None:
    head(5, "切片 slicing", "C# 的 s[1..5] / s[^3..]")

    s = "abcdefg"
    note("Python: s[起:止:步] —— 止是【不包含】的开区间，和 C# 的 .. 一样")
    print()
    for expr, result in [
        ("s[1:5]", s[1:5]),
        ("s[:3]", s[:3]),
        ("s[3:]", s[3:]),
        ("s[-3:]", s[-3:]),
        ("s[:-2]", s[:-2]),
        ("s[::2]", s[::2]),
        ("s[::-1]", s[::-1]),
        ("s[10:20]", s[10:20]),
    ]:
        print(f"       {expr:12} -> {result!r}")
    note("")
    note("对照 C#：s[1..5] ≈ s[1:5]；s[^3..] ≈ s[-3:]；s[..3] ≈ s[:3]")
    note("Python 独有：步长 s[::2]、反转 s[::-1]（C# 要 Enumerable.Reverse）")
    note("越界不抛异常！s[10:20] 返回空串 —— C# 会 IndexOutOfRange。切片是『能拿多少拿多少』。")

    xs = [10, 20, 30, 40, 50]
    note("")
    print(f"       xs[:]        -> {xs[:]}      ← 浅拷贝的惯用写法")
    print(f"       xs[:] is xs  -> {xs[:] is xs}  ← 不是同一个对象")
    note("       C# 对应: xs.ToList()")

    note("")
    note("--- 你代码里的真实用例 ---")
    text, chunk = "12345", 1
    note("streaming.py: yield text[i : i + chunk_size]")
    print(f"       当 i=0 时 text[0:1] -> {text[0:1]!r}；i=4 时 text[4:5] -> {text[4:5]!r}")
    note("       这就是『每次吐一个字符』。注意空格在冒号两侧是风格，不影响语义。")


# ---------------------------------------------------------------- 06 解包
@register(6, "解包、别名与对象身份")
def s06() -> None:
    head(6, "解包 / 别名", "C# 的 (a, b) = (b, a) / 引用类型语义")

    a, b = 1, 2
    a, b = b, a
    print(f"       交换 a, b = b, a  -> a={a}, b={b}")
    note("       右边先整体求值成元组 (2,1)，再解包赋值 —— 不需要 temp")

    first, *rest = [1, 2, 3, 4]
    print(f"       first, *rest = [1,2,3,4]  -> first={first}, rest={rest}")
    *init, last = [1, 2, 3, 4]
    print(f"       *init, last = [1,2,3,4]  -> init={init}, last={last}")
    note("       * 收集剩余部分（C# 没有直接对应，接近 Span 切片）")

    d1, d2 = {"a": 1}, {"b": 2, "a": 99}
    merged = {**d1, **d2}
    print(f"\n       {{**d1, **d2}} -> {merged}")
    note("       后面的覆盖前面的（a 变成 99）—— 这就是配置合并的写法")

    note("")
    note("--- 别名（aliasing）：最容易出诡异 bug 的地方 ---")
    x: list[int] = []
    y = x
    x.append(1)
    print(f"       x = []; y = x; x.append(1)  ->  x={x}, y={y}")
    note("       惯用写法是同一行赋值：x = y = [] —— 效果一样，两者共用同一个列表。")
    note("       y 也变了！因为 x 和 y 指向【同一个】列表对象。")
    note("       C# 里 y = x 同样是引用赋值，但 C# 的默认值 new List 每次新建",
         )
    note("       所以这么写没事：int a = 0, b = 0;  但这么写就有事：var a = new List<int>(); var b = a;")

    n1 = n2 = 0
    n1 += 1
    print(f"       对比 n1 = n2 = 0; n1 += 1  ->  n1={n1}, n2={n2}  ← int 不可变，+= 造新对象")

    note("")
    note("--- is / == / 小整数缓存 ---")
    p, q = [1], [1]
    print(f"       [1] == [1] -> {p == q}   （值相等）")
    print(f"       [1] is [1] -> {p is q}   （不是同一对象）")
    r = 256
    t = 256
    print(f"       256 is 256 -> {r is t}      ← 小整数被缓存，别依赖这个")
    u = 1000
    v = 1000
    print(f"       1000 is 1000 -> {u is v}    ← 大整数不保证")
    note("")
    note("铁律：判 None / True / False 用 is，判值用 ==。")
    print(f"       None == None -> {None == None}；is 也成立，但 is 才是惯用法")


# ---------------------------------------------------------------- 07 类
@register(7, "类体只执行一次（C# 最易误解）")
def s07() -> None:
    head(7, "类与实例", "C# 的 class 体 vs Python 的 class 体")

    exec_log: list[str] = []

    class Probe:
        # ↓↓↓ 这一整块是【语句】，在 class 定义时执行一次，不是每次实例化执行
        exec_log.append("class 体执行了 1 次")
        total = 0  # 类属性 ≈ C# static field
        registry: list[int] = []  # ← 危险：所有实例共享这个 list

        def __init__(self, n: int) -> None:
            self.n = n  # 实例属性必须在方法里赋值（C# 可写在字段声明处）
            exec_log.append(f"__init__ 跑了 n={n}")

        def bump(self) -> None:
            type(self).total += 1  # 改类属性要用 类名.属性
            self.registry.append(self.n)

    p1 = Probe(1)
    p2 = Probe(2)
    p1.bump()
    p2.bump()

    note("执行日志（注意第一条只出现一次）：")
    for entry in exec_log:
        print(f"       · {entry}")
    print()
    note(f"Probe.total = {Probe.total}（两个实例共同累加，因为它是类属性）")
    note(f"p1.registry = {p1.registry}，p2.registry = {p2.registry}  ← 同一个 list！")
    note("")
    note("读代码时的关键动作：看到类体里 x = [...] 就要警惕，它等价于 C# 的 static 字段。")
    note("实例属性赋值只可能出现在 __init__ 或其他方法里（写 self.xxx = ...）。")

    note("")
    note("--- 读代码时 3 个常见装饰器 ---")

    class Demo:
        _v = 10

        @property  # C# 的 `public int V => _v;`
        def v(self) -> int:
            return self._v

        @classmethod  # 第一个参数是 cls（整个类），≈ C# static 方法
        def make(cls, n: int) -> "Demo":
            d = cls()
            d._v = n
            return d

        @staticmethod  # 没有 self，就是个放在命名空间里的普通函数
        def helper() -> str:
            return "no self"

    note(f"        @property  → 访问 d.v（不加括号）得到 {Demo.make(7).v}")
    note(f"        @classmethod → Demo.make(7) 返回 {type(Demo.make(7)).__name__} 实例")
    note(f"        @staticmethod → Demo.helper() -> {Demo.helper()!r}")
    note("")
    note("注意：__init__ 不是 C# 的构造函数（不负责分配内存），它只是初始化器；")
    note("真正的分配在 __new__ 里 —— 日常不用管，但读别人代码时别被名字骗了。")


# ---------------------------------------------------------------- 08 装饰器
@register(8, "装饰器：@ 就是一层的函数调用")
def s08() -> None:
    head(8, "装饰器 decorator", "C# 的 Attribute / Middleware / AOP")

    def logged(fn):
        @functools.wraps(fn)  # 把原函数的 __name__/__doc__ 复制过来
        def wrapper(*args, **kwargs):
            print(f"       [wrapper] 调用 {fn.__name__} 前")
            result = fn(*args, **kwargs)
            print(f"       [wrapper] 调用 {fn.__name__} 后")
            return result

        return wrapper

    @logged
    def add(x: int, y: int) -> int:
        """两数相加。"""
        return x + y

    note("@logged 的完整含义：add = logged(add)")
    note("即：定义完 add，立刻把它整个塞进 logged()，用返回值【替换】名字 add。")
    print()
    print(f"       add(1, 2) = {add(1, 2)}")
    print(f"       add.__name__ = {add.__name__!r}  ← 靠 functools.wraps 保住的")
    note("")
    note("★ 读多层装饰器的顺序：从下往上包，从上往下执行。")
    note("   @A")
    note("   @B")
    note("   def f(): ...   等价于  f = A(B(f))")

    note("")
    note("--- 带参数的装饰器：多套一层 ---")
    note("   @app.get('/health')  →  health = app.get('/health')(health)")
    note("   ① 先算 app.get('/health')，返回一个『装饰器函数』")
    note("   ② 再用它包 health")
    note("   所以 @app.get(...) 这种写法 = 带参数的装饰器，参数给了外层。")

    note("")
    note("--- 你代码里的真实用例 ---")
    note("   config.py:  @lru_cache              → 把函数换成带缓存的版本")
    note("   errors.py:  无装饰器，靠类属性 retryable 做多态")
    note("   api.py:     @app.get('/health')      → 把函数登记进路由表")
    note("   deps.py:    @... 无 —— Depends 不是装饰器，是参数默认值机制")


# ---------------------------------------------------------------- 09 with
@register(9, "with：C# 的 using")
def s09() -> None:
    head(9, "上下文管理器 with", "C# 的 using / IDisposable")

    @contextmanager
    def timed(label: str) -> Iterator[None]:
        """把一个生成器变成上下文管理器：yield 前 = 进入，yield 后 = 退出。"""
        print(f"       · 进入 {label}")
        try:
            yield
        finally:
            print(f"       · 退出 {label}（异常也会走到这里）")

    note("@contextmanager 的本质：yield 之前是 __enter__，之后是 __exit__。")
    print()
    with timed("A"):
        print("       · with 体内（这是『退出』前会先跑的部分）")

    note("")
    note("--- with 的三种常见形态 ---")
    note("   ① with open(p) as f:            → 出块自动 f.close()")
    note("   ② with a, b:                     → 多个一起管（C# 要嵌套 using）")
    note("   ③ with suppress(KeyError):       → 吞掉指定异常")
    empty: dict[str, int] = {}
    with suppress(KeyError):
        empty["nope"]
    note("       with suppress(KeyError): 空字典['nope']  → 没抛出来，安静通过")

    note("")
    note("读代码提示：with 块的『收益』在块结束那一刻才体现（关连接、提交事务、释放锁）。")
    note("所以看到 with 就要问一句：退出时要做什么？")


# ---------------------------------------------------------------- 10 迭代
@register(10, "迭代协议与生成器")
def s10() -> None:
    head(10, "迭代协议 / 生成器", "C# 的 IEnumerable / yield return")

    class Counter:
        """自定义可迭代对象：for 循环背后到底调了什么。"""

        def __init__(self, n: int) -> None:
            self.n = n

        def __iter__(self) -> Iterator[int]:
            for i in range(self.n):
                yield i

    note("for x in obj: 展开成：")
    note("   it = iter(obj)            → 调 obj.__iter__()")
    note("   循环 it.__next__()        → 每次拿一个")
    note("   直到抛 StopIteration      → 循环结束（不是靠长度判断！）")
    print(f"\n       list(Counter(3)) -> {list(Counter(3))}")

    note("")
    note("★ 关键认知：Python 的 for 靠【异常】结束，不靠 Count/Length。")
    note("  这就是为什么生成器能表示『无限序列』，而 C# 的 IEnumerator 也一样（MoveNext 返回 false）。")

    note("")
    note("--- 生成器 vs 列表：内存差多少 ---")
    n = 100_000
    lst = [i for i in range(n)]
    gen = (i for i in range(n))
    print(f"       列表 [{n} 个 int]  内存约 {sys.getsizeof(lst) / 1024:.0f} KB")
    print(f"       生成器表达式      内存约 {sys.getsizeof(gen)} 字节")
    note("       差 3 个数量级 —— 这就是流式输出敢处理长文本的原因。")

    note("")
    note("--- 生成器只能走一遍 ---")
    g = (i for i in range(3))
    print(f"       第一次 list(g) -> {list(g)}")
    print(f"       第二次 list(g) -> {list(g)}   ← 空的，已耗尽")
    note("       读代码时若同一个生成器被用了两次，第二次一定是空的 —— 高频 bug。")

    note("")
    note("--- 你代码里的真实用例 ---")
    note("   streaming.py: async def tokenize(...) 里有 yield")
    note("     一旦函数体出现 yield，它【不再是普通函数】，调用它得到的只是生成器对象，")
    note("     一行代码都不会跑，直到有人开始迭代它。")


# ---------------------------------------------------------------- 11 async
@register(11, "async/await：和 C# 像在哪、差在哪")
def s11() -> None:
    head(11, "async / await", "C# 的 async Task / await / IAsyncEnumerable")

    ran: list[str] = []

    async def work() -> str:
        ran.append("work 真的开始跑了")
        await asyncio.sleep(0.01)
        ran.append("work 跑完了")
        return "ok"

    note("① 调用 async 函数【不执行任何代码】，只造一个协程对象：")
    coro = work()
    print(f"       造出来的是 {type(coro).__name__}，此时 ran = {ran}")
    note("       这跟 C# 不一样：C# 的 Task 是『已启动』的，Python 的协程是『待启动』的。")
    note("       所以忘了 await 不会报错，只会静默不执行（并给一个 RuntimeWarning）。")
    coro.close()  # 关掉它，免得报 never awaited 警告

    note("")
    note("② 必须交给事件循环：")
    result = asyncio.run(work())
    print(f"       asyncio.run(work()) -> {result!r}，ran = {ran}")

    note("")
    note("③ 对照表：")
    for py, cs in [
        ("async def f()", "async Task F()"),
        ("await f()", "await F()"),
        ("asyncio.gather(a, b)", "await Task.WhenAll(a, b)"),
        ("asyncio.sleep(n)", "await Task.Delay(n)"),
        ("asyncio.Semaphore(n)", "SemaphoreSlim(n)"),
        ("async for x in agen", "await foreach (var x in aenum)"),
        ("async with ...", "await using ..."),
        ("asyncio.run(main())", "Main 里 .GetAwaiter().GetResult()"),
    ]:
        print(f"       {py:26} {cs}")

    note("")
    note("④ 两个 Python 特有的坑：")
    note("   · 事件循环是【单线程】的：协程里写 time.sleep(5) 会冻住所有人（其他协程全卡）。")
    note("     阻塞调用要用 asyncio.sleep / await asyncio.to_thread(...)。")
    note("   · 真正并发的判据是『await 时是否把控制权交回去』。")
    note("     你 W1 Day 4 实测 15.04s → 6.01s，省下来的时间就是交还控制权换来的。")

    note("")
    note("--- 你代码里的真实用例 ---")
    note("   streaming.py: async def sse_chat_stream(...) -> AsyncIterator[str]")
    note("     它是『异步生成器』：既有 async（可 await）又有 yield（可分多次产出）。")
    note("     所以调用它要 async for 而不是 for；要 await 的是它内部的 send。")
    note("   deps.py: Callable[[], Awaitable[bool]] 就是 『一个返回可 await 对象的无参函数』")
    note("     对应 C# 的 Func<Task<bool>>。")


# ---------------------------------------------------------------- 12 糖
@register(12, "语法糖与真值判断速查")
def s12() -> None:
    head(12, "零碎语法糖 + 真值表", "C# 的 ?: / ?? / switch / 隐式 bool")

    x, cond = 5, True
    print(f"       三元：{x} if {cond} else 0  -> {x if cond else 0}")
    note("       C#: cond ? x : 0  —— 注意 Python 把结果放前面，条件在中间")

    data = {"a": 1}
    print(f"       空值兜底：data.get('b', 0) -> {data.get('b', 0)}")
    note("       C#: data.TryGetValue(...) ? v : 0  或 dict?.a ?? 0")

    if (m := 10) > 5:  # 海象运算符：赋值并返回
        print(f"       海象 (m := 10) > 5  -> 成立，m = {m}")
    note("       赋值表达式 := 让你在 if / while 条件里顺手赋值，C# 没有对应物")

    print(f"       链式比较 0 < {x} < 10 -> {0 < x < 10}")
    note("       C#: 0 < x && x < 10 —— Python 直接两条比较连起来写")

    note("")
    note("--- 真值表（Python 的 if x: 到底怎么看）---")
    for v in [0, 0.0, "", [], {}, set(), (), None, False, 1, "0", [0], -1]:
        print(f"       bool({v!r:>8}) = {bool(v)}")
    note("")
    note("★ 读代码最容易错的一处：")
    note("   if xs:        → 『xs 非空』（不是『xs 存在』）")
    note("   if xs is None:→ 『xs 不存在』")
    note("   if xs is not None: → 『哪怕它是空列表也算存在』")
    note("   你 stub_llm.py 里 `if settings is not None` 就是这个精确语义。")

    note("")
    note("--- match（3.10+）与 C# switch 的两处不同 ---")
    note("   ① 没有 fallthrough，命中即结束，不需要 break")
    note("   ② 它能做【结构匹配】，不只是值匹配：")
    note("      case {'type': 'token', 'text': t}:   ← 直接匹配字典形状并绑定 t")
    note("   本项目暂未使用，认识即可。")


# ---------------------------------------------------------------- 13 模块
@register(13, "模块执行模型：import 到底发生了什么")
def s13() -> None:
    head(13, "模块执行模型", "C# 的 namespace + 编译期联结")

    note("Python 的一个 .py 文件 = 一个模块 = 一个『从上往下执行一次的脚本』。")
    note("import ai_lab.config 做的事：")
    note("   ① 找到文件  ② 逐行执行它  ③ 把产生的名字挂到模块对象上  ④ 缓存（第二次 import 不重跑）")
    note("")
    note("★ 由此推出三条读码规则（你的 C# 直觉会全部失效）：")
    note("   1. def / class 本质是『赋值语句』：def f() 就是 f = <函数对象>")
    note("      → 所以【必须先定义再使用】。C# 方法顺序无关，Python 顺序有所谓。")
    print()
    note("      错误示例（会 NameError）：")
    note("        def a(): return b()   # 这行没事，因为只是定义")
    note("        a()                   # 这行在 b 定义之前 → NameError")
    note("        def b(): return 1")
    note("")
    note("   2. 模块顶层写着 print(...) 会在【import 时】执行，不是运行时")
    note("      → 所以你 W1 踩的那个坑：import config 把 API Key 打到控制台。")
    note("   3. `if __name__ == '__main__':` 的含义：")
    note(f"      直接运行本文件时  __name__ = {__name__!r}   （会进这个块）")
    note("      被别的模块 import 时  __name__ = 'ai_lab.xxx'（不进这个块）")
    note("      C# 里没有对应概念（有 static Main 的入口约定），"
         "可以理解成『仅当自己是入口才跑』。")

    note("")
    note("--- 相对路径的坑（你也踩过）---")
    note("   open('.env') 永远按【运行时工作目录 cwd】解析，不是按文件位置。")
    note("   所以 config.py 用的是 Path(__file__).resolve().parents[2] / '.env'：")
    note("     __file__            → 当前文件路径（字符串）")
    note("     Path(__file__)      → 包成路径对象")
    note("     .resolve()          → 转成绝对路径，消掉 .. 和软链")
    note("     .parents[2]         → 往上两级：ai_lab/ → src/ → 项目根")
    note("   C# 里 AppDomain.CurrentDomain.BaseDirectory 是同一类需求。")


# ---------------------------------------------------------------- 主入口
def main(argv: list[str]) -> int:
    wanted = [int(a) for a in argv if a.isdigit()]
    if wanted:
        unknown = [n for n in wanted if n not in CLI_SECTIONS]
        if unknown:
            print(f"未知小节：{unknown}。可用：{sorted(CLI_SECTIONS)}")
            return 1
        targets = wanted
    else:
        targets = sorted(CLI_SECTIONS)
        print(f"{LINE}\nPython 语法导览（C# 对照版）  —— 共 {len(targets)} 节\n{LINE}")
        print("可用：python scripts/syntax_tour.py 4      只看第 4 节")
        print("      python scripts/syntax_tour.py 4 8 11  看多节")

    for n in targets:
        fn = getattr(sys.modules[__name__], f"s{n:02d}")
        fn()

    print(f"\n{LINE}\n跑完 {len(targets)} 节。\n{LINE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
