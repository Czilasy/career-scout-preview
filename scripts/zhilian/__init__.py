# -*- coding: utf-8 -*-
"""智联抓取域包（028 新建）：超标文件 scripts/zhilian_cdp_raw.py 的分流落位。

031 B6 后域模块划分（引用方向：其余域模块 → ``cdp`` 单向，互不回溯门面）：

- ``cdp``：CDP 连接原语（HTTP/WS、求值、导航、后台标签）与平台常量
  （端口、host allowlist、探测 URL、提取 JS、风险 marker）；
- ``search``：列表域（登录探测 / preflight / fetch_list / 风险信号 / 岗位归一）；
- ``detail``：详情域（单条提取 / tab 池并行批量 / 会话重置）；
- ``urls``：纯函数域（host 判定 / input_hash）；
- ``detail_fields``：staff 字段提取（028 B081）。

``scripts/zhilian_cdp_raw.py`` 为兼容门面，只做 re-export 代理。
"""
