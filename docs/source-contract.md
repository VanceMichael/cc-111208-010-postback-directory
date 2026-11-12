# 合成网点门户

GET /directory 建立 cookie 会话并返回表单和网点表。POST action 为相对路径，控件中包含重复 scope、隐藏 state、city 下拉框及多个提交按钮。successful controls 保留多值，隐藏字段、选中选项、勾选项和 textarea；没有显式选择时单选框使用首选项。省略 disabled、未勾选、无 name 控件，只加入实际触发按钮。允许 city 与 next，禁止 reserve/logout。一次性 state 在每次响应后更新，不能跨城市或重用；重启可重新 GET，但不能把过期状态当作下一页。

table 的 data-city、data-total、data-last 表示城市分区、去重后的总数和终点；tr 的 data-channel/data-store 是网点身份，城市不是业务键。源页面固定 total，重复页、登录表单或总数不符都不证明完成。跨城共享网点内容一致则合并，冲突要保留来源并等待所有配置城市的核对。

scripts.source_server 提供最小页面及过期请求回放，scripts.portal 提供可重复建会话、切城和翻页的完整本地门户。后者可用 --fault-city B 模拟第二城市计数不符，或 --conflict 模拟邻城共享网点内容冲突。原文按内容寻址，城市进度持久化；重走当前城不增加重复原页或网点。请求及重定向限制在 origin 的 /directory，不做真实登录或预约。
