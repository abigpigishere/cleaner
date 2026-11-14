# 第一步：只验证 NTFS 文件记录的高速枚举

第二步已另行实现：见 [SIZES.md](SIZES.md)，其中是原始 FILE 记录的小批量大小解析及与 Windows 对照结果。下文保留第一步枚举 demo 的范围和结果。

目标：先测读取机制，不实现 UI、可视化、删除、skill 安装或自动清理。

WizTree 官方说明其 NTFS 加速来自直接读取 MFT。这里先采用 Windows 文档公开的 `FSCTL_ENUM_USN_DATA`：通过一个只读卷句柄，批量取得由文件系统返回的 MFT 文件记录信息，而非递归打开每个目录。它仍经过 Windows 文件系统驱动，不是 WizTree 源码，也不是原始 `$MFT` 数据流解析器。

本 demo 不使用 REA、不运行 WizTree，也不需要任何第三方依赖；Python 3 标准库即可。

## 运行

在管理员 PowerShell 中：

```powershell
python 'E:\桌面\cleaner\mft_demo.py' --drive E: --output 'E:\桌面\cleaner\results\e-full.json'
```

输出路径必须是新文件；重复测量改名即可。`--max-batches 1` 可做单批检查；这种结果标为 limited，不能当作全盘测量。`--samples 0` 不保存样本名称。当前进程没有卷访问权限时，返回 Windows 错误码和管理员终端提示，不偷偷降级成递归目录扫描，也不创建/修改 USN 日志。

```powershell
python -m unittest discover -s 'E:\桌面\cleaner' -p 'test_mft_demo.py' -v
```

## 观察什么

- records / file_records / directory_records：本次 API 返回的记录数，不能等同所有路径数量。
- elapsed_seconds / records_per_second：批量读取和 Python 解析的总耗时及吞吐，不含 JSON 写入，不是完整磁盘空间分析的耗时。
- completed：只有读到系统 EOF 才为 true；limited、cancelled、error 都不是完整结果。
- samples：文件 ID、父 ID、名称和属性；不读取文件内容，不跟随重解析点。
- bytes_returned：内核返回的记录字节，不是物理磁盘读取字节。

当前没有对照递归扫描、WizTree 的测试，因此不能声称快几倍；单次测量也受到系统负载和缓存影响。

## 本机实测（2026-11-14）

普通权限访问 E 盘得到 WinError 5；管理员权限测试正常读到 EOF，进程退出码 0。

| 指标 | 结果 |
|---|---:|
| 返回记录 | 706,273 |
| 文件记录 | 627,272 |
| 目录记录 | 79,001 |
| 批次 | 70 |
| 枚举与解析耗时 | 2.876796 秒 |
| 吞吐 | 245,507 条/秒 |

测量原始结果：`results/e-admin-full.json`。这是一次全范围枚举的单次测量，不是经过冷热缓存控制的性能基准。三项解析测试通过，另已核对记录总数等于文件记录加目录记录。未执行目录递归对照；本次也没有分析 WizTree 二进制。

## 明确的技术缺口

USN_RECORD_V2/V3 没有文件逻辑大小/分配大小。这一步不统计磁盘占用，不生成“系统及其他”，也不能验证“已找到所有占用”。同一个文件的所有硬链接名称、备用数据流、驻留数据、稀疏/压缩文件、实时变化和系统元数据仍需要后续处理。

下一步应根据真实枚举结果决定是否实现批量原始 MFT 读取与 `$DATA` 属性解析，再验证文件逻辑大小、分配大小和硬链接计数。避免逐文件补查把批量读取的优势抵消。只有大小口径可靠之后，再做目录聚合和可视化。

## 原始资料

- WizTree 原理：https://diskanalyzer.com/about
- Windows 枚举接口：https://learn.microsoft.com/en-us/windows/win32/api/winioctl/ni-winioctl-fsctl_enum_usn_data
- 输入结构：https://learn.microsoft.com/en-us/windows/win32/api/winioctl/ns-winioctl-mft_enum_data_v0
- 返回记录 V2：https://learn.microsoft.com/en-us/windows/win32/api/winioctl/ns-winioctl-usn_record_v2
- 返回记录 V3：https://learn.microsoft.com/en-us/windows/win32/api/winioctl/ns-winioctl-usn_record_v3
