"""`python -m ai_lab` 的入口。

没有这个文件时，`python -m ai_lab` 会执行 __init__.py 里的语句——
那里只有函数定义，于是什么都不发生（容器起来后立刻静默退出）。
"""

from ai_lab import main

if __name__ == "__main__":
    main()
