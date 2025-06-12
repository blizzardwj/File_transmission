"""
两种写法的核心区别在于装饰器自身在类中的绑定方式和可读性／可维护性：

1. **第一种（裸函数 `def method_decorator`）**  
   - `method_decorator` 直接定义在类体内，没有加 `@staticmethod` 或 `@classmethod`，在类创建时它只是一个普通函数。  
   - 用法虽然能工作，但容易让人混淆：读者会以为它是实例方法，却又无法通过 `self` 调用。  
   - 缺少 `functools.wraps`，会丢失原函数的 `__name__`、`__doc__` 等元信息。

2. **第二种（`@staticmethod` + `functools.wraps`）**  
   - 用 `@staticmethod` 明确告诉 Python 这是一个不依赖类／实例状态的函数，仅用来生成 wrapper。  
   - 在 wrapper 里用 `functools.wraps(func)` 保留被装饰函数的元信息，更加「Pythonic」。  
   - 意图清晰：类成员中只留真正的逻辑方法，其它工具方法（装饰器）都被标识为静态方法。

结论：第二种写法更规范也更优雅，推荐在类中把装饰器定义为 `@staticmethod`（或顶层函数），并且配合 `functools.wraps` 来实现。
"""

# 定义一个类并使用装饰器
class MyClass:
    def __init__(self):
        self.internal_property = 0

    def method_decorator(func):
        def wrapper(self, *args, **kwargs):
            # 修改内部属性
            self.internal_property += 11
            result = func(self, *args, **kwargs)
            # 可选的修改属性
            self.internal_property -= 1
            return result
        return wrapper

    @method_decorator
    def my_method(self):
        print(f"Internal property value: {self.internal_property}")

# 使用类
obj = MyClass()
obj.my_method()

import functools

class BufferManager:
    def __init__(self):
        self._cumulative_bytes = 0
        self._cumulative_time = 0.0

    @staticmethod
    def accumulate_stats(func):
        @functools.wraps(func)
        def wrapper(self, bytes_transferred, transfer_time):
            # 操作实例属性
            self._cumulative_bytes += bytes_transferred
            self._cumulative_time += transfer_time
            return func(self, bytes_transferred, transfer_time)
        return wrapper

    @accumulate_stats
    def adaptive_adjust(self, bytes_transferred: int, transfer_time: float) -> float:
        # 这里可以继续使用 self._cumulative_bytes/_time
        buffer_rate = bytes_transferred / transfer_time if transfer_time > 0 else 0
        return buffer_rate  # 举例返回

    def get_average_transfer_rate(self) -> float:
        if self._cumulative_time > 0:
            return self._cumulative_bytes / self._cumulative_time
        return 0.0

# 这是一个简单的测试类，演示如何使用装饰器修改内部属性
buffer_manager = BufferManager()
buffer_manager.adaptive_adjust(1024, 0.5)
print(f"Average transfer rate: {buffer_manager.get_average_transfer_rate()} bytes/sec")
