# servicepostback

保养网点门户的 HTML 表单查看、原文留存与授权页面采集工具。

## 本地运行

使用 Python 3.11 标准库，无 pip 依赖。所有来源与业务数据都是本地合成数据，不连接真实用户系统。

```sh
python -m scripts.materialize
python -m servicepostback inspect fixtures/city-a.html
python -m compileall -q .
python -m unittest discover -s tests -v
python -m scripts.demo
```

`inspect` 只输出单份文件的结构摘要。`capture URL --method GET --state var` 仅接受 loopback HTTP，保存原始响应为 SHA256 文件，并在 SQLite captures 表登记；可用 `--header 'Name:Value'` 传头、`--body FILE` 传正文，不跟随重定向。

## 授权采集（harvest）

`harvest` 只按门户实际返回的页面交互：GET `/directory` 取得会话与表单，之后 POST 当前表单的 successful controls，并只加入实际触发的按钮。它不构造城市/分页 URL，不解析脚本内的私有接口，不调用 `reserve` 或 `logout`；请求与重定向都限制在授权 origin 的 `/directory` 内。

```sh
python -m servicepostback harvest http://127.0.0.1:8765/directory --state var/run
python -m servicepostback progress --state var/run
python -m servicepostback site --state var/run
python -m servicepostback site --state var/run --channel C1 --store S1
```

- `harvest --city A --city B` 只采指定城市；省略时采表单声明的全部城市。城市必须来自表单下拉框。
- 每次响应后都使用该响应发回的新 state；切城后重新 GET 并用新页面字段，过期状态返回 409 时重走当前城市。
- 城市只有在其末页且 `data-last`、`data-total` 与去重计数一致时才标记 `complete`；登录页、重复页、计数不符都不会被当成成功结束。
- 已完成城市再次运行不会重走，原页与网点不会重复增加；未完成城市重走时，原页按（城市, 页码）覆盖而不是追加。
- 网点身份为 `data-channel`/`data-store`；城市只是分区。各出现城市都被证明完整且内容一致才合并发布；内容冲突则保留双方来源页并标记 `conflict`，不发布该网点；出现城市未完成时标记 `held`。
- `site --channel C1 --store S1` 返回该网点的每个来源城市、页码、原文摘要与城市状态；原页正文在状态目录 `blobs/` 下按 SHA256 内容寻址。只有只读查询，不执行预约或登录。

`scripts/portal` 是可重复建会话、切城、翻页的本地门户。故障开关用于演示和测试：`--stale-after N`（一次性过期后恢复）、`--conflict`（邻城共享网点内容冲突）、`--fault-city B`（计数不符）、`--repeat`（重复页）、`--no-city-switch`（缺少切城控件）、`--login`（登录页）、`--redirect`（越界重定向）、`--cities A,B`。`scripts/source_server` 提供 fixtures/source.json 的最小回放与 `/__requests` 请求记录。

## 可复现容器

```sh
docker build -t servicepostback:local .
docker run --rm --network none servicepostback:local python -m unittest discover -s tests -v
docker run --rm --network none servicepostback:local
```

镜像固定基础摘要。构建在正式运行前完成；运行和测试不安装包、不下载数据。演示命令使用新的临时状态目录，执行 `inspect`、带一次过期状态恢复的双城采集、进度与来源页查询、邻城冲突留证，完整输出通常不超过 5 秒。

## 协议与来源

- docs/source-contract.md 给出支持子集、字段及失败语义。
- docs/acceptance.md 给出业务验收边界。
- fixtures/source.json 是本地可重放 HTTP 路由；`python -m scripts.source_server --port 8765` 启动，`/__requests` 可检查实际请求。
- scripts/materialize.py 将版本化 base64 二进制夹具还原为原始字节，重复执行安全；二进制文件不是用户附件。
- journal.py 只记录原始响应；状态目录中的 harvest 表（页面索引、城市进度、网点）由 `servicepostback/store.py` 在同一 SQLite 数据库中事务维护。
