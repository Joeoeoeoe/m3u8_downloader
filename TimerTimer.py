# 定时执行某个函数func，可以设置定时重复执行

import threading

class TimerTimer:
    def __init__(self, interval, func ,repeat=True):
        self.timer = None
        self.interval = interval

        self.func = func  # 用户传入的函数
        self.call_count = 0  # 记录调用次数

        self.repeat = repeat  # 是否循环执行
        self.is_running = False  # 控制循环执行的标志

    def StartTimer(self):
        if not self.is_running:
            self.is_running = True
            self._schedule_timer()

    def _schedule_timer(self):
        # 内部方法：调度定时器
        self.timer = threading.Timer(self.interval, self._func_wrapper)
        self.timer.start()

    def _func_wrapper(self):
        # 包装函数，用于计数并调用用户函数
        self.call_count += 1
        try:
            self.func()
        except TypeError as e:
            if 'missing 1 required positional argument' in str(e):
                self.func(self.call_count)
            else:
                raise
        # 递归重复定时器
        if self.repeat and self.is_running:
            self._schedule_timer()  # 重新调度下一次执行

    def StopTimer(self):
        self.is_running = False
        if self.timer is not None:
            self.timer.cancel()

    def ResetCounter(self):
        # 重置计数器
        self.call_count = 0
