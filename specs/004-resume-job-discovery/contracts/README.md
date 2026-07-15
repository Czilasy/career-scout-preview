# Contracts: 简历驱动的岗位发现

- [openapi.yaml](openapi.yaml)：默认四步旅程使用的 HTTP 契约。
- [ai-contracts.md](ai-contracts.md)：候选人分析 v2、确定性证据定位和岗位方向评估的 AI 输入输出边界。
- [state-machine.md](state-machine.md)：分析、运行、搜索项、详情和评估状态转换。

契约原则：

1. 用户可见和持久化响应不返回简历正文、API Key、凭据引用或模型原始输出。
2. 所有写操作继续使用现有本地会话保护。
3. 失败响应统一使用安全错误码、用户消息、失败阶段和可重试标记。
4. 旧 `/api/search-runs` 与 `/api/screening` 契约保留为兼容接口，不在本文件中改写。
5. 用户入口必须经过真实 provider 构建和运行调度边界；内部函数直调、fake provider 和手工状态写入不能作为 HTTP 或端到端契约通过证据。
