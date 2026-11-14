# 第三步：原始 MFT 分块扫描

性能热路径已升级为 C++：见 [PERFORMANCE.md](PERFORMANCE.md)，新入口为 fast_scan.py。以下保留 Python 参考实现与当时实测。

已实现只读扫描内核 demo，入口为 `bulk_mft.py`。不包含界面、清理操作或 skill 安装。

## 实际读取路径

```text
只读打开 NTFS 卷
  → 查询 NTFS 几何参数和 MFT 有效长度
  → 用少量 FSCTL_GET_NTFS_FILE_RECORD 调用取得 $MFT 本身的记录
  → 解析 $MFT 的未命名 $DATA 存储区段（run list）
  → 必要时根据 $ATTRIBUTE_LIST 获取 $MFT 自身的扩展记录
  → 按逻辑顺序，对各物理区段执行分块 ReadFile
  → 对每个原始 FILE 记录校验并恢复 USA
  → 解析名称、父目录引用、逻辑大小、外部数据分配大小和数据流
  → 分批写入 SQLite，输出覆盖率和耗时
```

主扫描不调用逐文件属性查询，不递归遍历目录。E 盘实测 MFT 分散在 4 个区段，已覆盖跨区段读取。启动和扫描结束各用一次记录查询确定映射，合计 2 次；不是对 62 万文件分别查询。当前映射所需扩展记录很少时也可用该接口辅助启动；设置了数量和读取长度边界。

读取卷的权限为 GENERIC_READ，没有写卷、锁卷、卸载卷、创建 USN 日志、修改文件权限或提取外部文件内容的操作。MFT 记录本身可能包含驻留文件内容，解析器在内存中取得记录，但不保存原始记录或驻留内容。

## E 盘最终实测（2026-11-14）

| 指标 | 结果 |
|---|---:|
| 初始有效 MFT 数据 | 832,045,056 字节（793.5 MiB） |
| 原始 ReadFile 调用 | 103 次 |
| 原始读取调用累计耗时 | 0.417521 秒 |
| 解析与 JSON 编码 | 16.809025 秒 |
| SQLite 批量写入 | 2.427599 秒 |
| 扫描总耗时 | 20.568181 秒 |
| 扫描记录槽 | 812,544 |
| 有效基本文件/目录记录 | 706,359 |
| 文件 | 627,348 |
| 目录 | 79,011 |
| 已解析文件主流大小 | 627,126（99.9646%） |
| 未解析文件大小 | 222 |
| 记录解析错误 | 0 |

记录槽同时包含未使用记录和扩展记录，不能把 812,544 当作文件数量。结束时再次查询的 MFT 区段映射及有效长度均与开始一致。

读取时间是 Windows API 调用的实测累计时间，可能受系统缓存影响，不等同 SSD 实际介质读取耗时。总耗时包括扫描初始化、读取、解析、编码和索引批量写入，不包括 UAC 等待、Python 启动及最终很小的报告写出。未与 WizTree 做同条件对照；不能据此声称达到同等速度。当前主要瓶颈是 Python 解析与编码。

最终数据位于：

- `results/bulk-e-full-v2/report.json`：范围、分块映射、分类计数、覆盖率和耗时。
- `results/bulk-e-full-v2/index.sqlite`：706,359 条基本记录，约 236 MiB。
- `results/bulk-e-full-v2/verification.json`：与上一阶段 8 个样本的引用、名称、数据流及大小核对结果，全部通过；记录总数和数据库计数等 5 项一致性检查通过。
- `results/bulk-e-full-v2/progress.json`：当前/最终状态。

SQLite 的 `records` 表按 record_number 唯一保存基本记录，硬链接不会使同一个文件的数据在该表重复计数。名称和父引用在 names_json，数据流信息在 streams_json；完整路径及目录聚合尚未构建。扫描记录包含所有成功解析的大小级别，不过滤小文件。元数据属于本地个人文件目录信息，不宜直接公开分享。

## 未解析项的含义

- 218 个文件含 `$ATTRIBUTE_LIST`，尚未实现普通文件的跨扩展记录属性合并，逻辑/分配大小明确存为 NULL。
- 4 个文件的主数据流缺失或不完整，大小同样保留 NULL。不能根据文件名称或缺失流推断其占用为 0。
- 目录大小当前为 NULL；目录索引空间、命名流和 NTFS 系统元数据没有合并成全卷“已用空间”。已知主流大小的累计值不等同系统已用空间，也不等同可删除释放量。
- 原始卷是动态数据源，不是时间点快照；USA 校验、开始/结束区段一致和样本一致不证明扫描期间所有文件都未变化。
- 全范围扫描覆盖的是开始时的 MFT 有效范围；若结束时范围增长，报告会反映长度变化。区段映射变化则标为 mapping_changed。

初次完整测试发现 32 个空非驻留数据流使用 LowestVCN=0 / HighestVCN=-1 的合法空 run list 表示，已修正解析并添加测试，最终重跑零解析错误。没有通过跳过校验或把错误大小强设为 0 来消除失败。

## 运行

管理员 PowerShell 中运行，输出必须是新目录：

```powershell
python 'E:\桌面\cleaner\bulk_mft.py' --drive E: --output-dir 'E:\桌面\cleaner\results\my-e-scan'
```

只验证一小段读取可加 `--max-records 4096`；结果为 limited，不能当作全范围扫描。`--block-mib` 支持 1、2、4、8、16，默认 8。不支持的卷、映射、记录和权限错误会明确报告，不降级为递归扫描。

```powershell
python -m unittest discover -s 'E:\桌面\cleaner' -p 'test_*.py' -v
```

19 项测试通过，包含碎片映射、跨区段逻辑读取、有符号区段偏移、稀疏区段拒绝、空非驻留表示、属性边界和损坏 USA 校验。当前全量实机验证只做了这台机器的 E 盘 NTFS 3.1，不能视为所有 Windows/NTFS 卷已验证。

## 下一阶段

先补普通文件的扩展记录合并、明确数据流/目录索引计量口径，并对解析热点做性能剖析；再决定是否将解析热路径移到编译型实现。分块读取层已与解析层分开，可以分别改进而不提前引入界面。

## 依据

- https://learn.microsoft.com/en-us/windows/win32/api/winioctl/ns-winioctl-ntfs_volume_data_buffer
- https://learn.microsoft.com/en-us/windows/win32/devnotes/attribute-record-header
- https://learn.microsoft.com/en-us/windows/win32/devnotes/attribute-list-entry
- https://learn.microsoft.com/en-us/windows/win32/devnotes/multi-sector-header
