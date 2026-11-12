# 接收流程核验

原有版本只提供原文留存与单份检查。`harvest` 是新增的独立业务流程，由 `tests/test_harvest.py` 独立覆盖端到端行为；`tests/test_baseline.py`、`tests/test_portal.py` 只证明原有检查器与本地门户的行为。

- successful controls 保留 scope 多值，隐藏 state 每次响应后更新，disabled/未勾选/无 name 控件省略，只加入实际触发按钮；预约按钮不得提交
- 每次 POST 用上一响应发回的 state；切城后重新 GET 并使用新页面字段，过期后重走当前城且完整城市不重复、原页按页码覆盖
- 登录页/重复页/总数不符拒绝完成；同一城市重复网点、声明总数变化或末页计数不符都使该城市失败并保留原页
- 跨城共享网点内容一致合并发布；冲突保留双方来源页并标记 conflict；出现城市未完成时标记 held
- 请求方法只允许 GET/POST，表单 action 与重定向必须留在授权 origin 的 /directory；不做真实登录或预约

## 演示与故障注入

允许扩充 fixtures/source.json。运行本地服务后可访问 /__requests 核对真实方法、请求头和正文。每次演示用新的临时状态目录；崩溃测试必须启动子进程并实际终止，不只模拟抛异常。

```sh
python -m scripts.portal --port 8765 --stale-after 1   # 过期状态后恢复
python -m scripts.portal --port 8765 --conflict        # 邻城共享网点冲突
python -m scripts.portal --port 8765 --fault-city B    # 第二城市计数不符
python -m scripts.demo                                 # 合成门户完整闭环
```

正式演示选择上述业务闭环中的一个，完整输出控制在 90 秒内；准备依赖和构建在演示前完成。`python -m scripts.demo` 实际执行切城、翻页、一次过期状态恢复、邻城重复网点合并，以及冲突模式下的来源页查询，通常不超过 5 秒。
