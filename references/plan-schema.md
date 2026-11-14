# Agent 建议方案

先通过 `cleaner_cli.py call api/plan` 或 `plan_tool.py --scan-dir <结果目录>` 导出当前方案。保留 `schema_version: 1`、`scan_id`、每项 `id` 和完整 `reference`。每项仅接受 `id/reference/operation/reason/evidence`。

`operation` 包含 `review/cache_cleanup/move/recycle/uninstall`，具体项目可用集合由本地分类器决定。agent 可改理由、证据和允许的操作，不能提供 arbitrary path、命令或新的大小替代扫描事实。

```json
{"schema_version":1,"scan_id":"从导出方案取得","items":[{"id":123,"reference":"从导出方案取得完整引用","operation":"review","reason":"已核实的用途与处置理由","evidence":["具体路径结构或应用信息"]}]}
```

导入：`python <skill>/plan_tool.py --scan-dir <结果目录> --import-plan <JSON文件>`，或 `cleaner_cli.py call api/plan --body-file <JSON文件>`。前端点“重新载入建议”。agent 方案不被默认规则刷新覆盖。

GET `api/advice` 返回前端丰富数据（`rows`、`allowed_operations`、`revision`、`scan_id`）。GET `api/plan` 导出核心方案。POST `api/preview` 的清单 `{scan_id,revision,items:[{id,operation}]}` 可以含手动选取的大文件，但仍在本次扫描范围内。`destinations` 以记录号字符串为键指定完整目标；`delete_modes` 的值是 `recycle` 或 `permanent`。预览不会执行操作，保存为 `preview.json`。
