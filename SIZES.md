# 第二步：从 MFT 记录解析文件大小

第三步已实现：见 [BULK.md](BULK.md) 的原始 MFT 分块扫描及 E 盘实测。以下保留逐记录正确性验证的范围和结果。

状态：小批量正确性 demo 已完成。没有界面、删除功能或全盘大小扫描；也没有安装 skill。

## 如何读到大小

1. 对指定文件打开只查询元数据的句柄，取得 NTFS 文件引用（记录号 + 序列号）。文件数据流不会被作为内容读取。
2. 以 GENERIC_READ 打开卷，使用 `FSCTL_GET_NTFS_VOLUME_DATA` 查询 NTFS 版本、扇区/簇/记录大小。
3. 用 `FSCTL_GET_NTFS_FILE_RECORD` 取得该文件的 FILE 记录。该接口可能返回编号更小的其他有效记录，因此同时核对返回编号与文件序列号。
4. 解析属性边界，列出 `$FILE_NAME` 与 `$DATA` 属性。逻辑大小取自主数据流的 FileSize；普通非驻留数据流分配大小取 AllocatedLength，压缩/稀疏数据流取扩展 TotalAllocated，并解析 run list 核对实际分配簇。
5. 小文件的 resident `$DATA` 直接存于 MFT 内：逻辑大小取 ValueLength，独立外部分配簇记 0。文件元数据与驻留内容所占的共享 MFT 存储需另行核算，不能把 0 外部分配理解为完全不占空间。
6. 查询 Windows 文件信息前后两次，检查本次比较期间的大小与链接数是否变化，再按文件类型使用相应字段核对。

扫描器只读源文件和卷元数据。原始 FILE 记录可能包含驻留数据，本 demo 在内存中接收记录，但不导出记录原始字节或驻留内容，只输出名称和大小等元数据。

## Windows 对照口径

- 逻辑大小：`FILE_STANDARD_INFO.EndOfFile`。
- 普通非驻留分配：`FILE_STANDARD_INFO.AllocationSize`，包含簇对齐产生的额外分配。
- 压缩/稀疏物理数据分配：`FILE_COMPRESSION_INFO.CompressedFileSize`。
- 驻留样本：Windows AllocationSize 在本机返回驻留内容长度。报告核对其与 MFT ValueLength 一致；外部簇为 0 是从 resident 布局解析得到的事实，不宣称 Windows 此字段独立验证了 0 字节物理占用。
- 硬链接：同一引用对应多个名称；核对记录头链接数与 Windows NumberOfLinks。本次两个路径对应同一条记录，后续汇总不能重复计算其数据簇。
- 命名数据流：单独列在 streams 中，主文件逻辑大小和分配大小仅指未命名 `$DATA`；本次额外识别了名为 demo、48 字节的驻留流，其内容不导出。本阶段未逐个核对命名流的 Windows API 结果，不把主流对照通过等同于全部流都验证过。

IOCTL 返回内存布局记录，本机压缩样本的 USA 保存值与扇区尾已不一致；不能对它重复应用原始磁盘 fixup。解析器显式区分 `source=ioctl` 与 `source=raw`；后者必须通过严格 USA 校验。此次没有从磁盘区段批量读取 MFT，合成 raw-layout 测试通过不等于该后续路径已实机验证。

## 本机验证，2026-11-14

11 项自动化测试通过；管理员只读卷查询进程正常退出，8 个样本的主流大小/相应分配口径/链接数全部对照通过。

| 样本 | 逻辑字节 | 外部分配字节 | 结果 |
|---|---:|---:|---|
| 空文件 | 0 | 0 | 通过 |
| 驻留小文件 | 128 | 0 | 驻留布局与 API 口径核对通过 |
| 普通文件 | 8,193 | 12,288 | 通过 |
| 普通文件 | 1,048,576 | 1,048,576 | 通过 |
| 硬链接路径 | 8,193 | 12,288 | 与普通文件共用同一记录，不可重复加总 |
| 稀疏文件 | 16,777,339 | 8,454,144 | MFT run list 与 Windows 均返回此值 |
| 压缩文件 | 2,097,147 | 131,072 | 通过 |
| 含命名流文件（仅主流） | 1,025 | 4,096 | 通过；另列 48 字节命名流 |

稀疏文件不代表只有写入的几个字节会分配空间：此次样本的实际布局仍分配了约 8.06 MiB，解析器保留实测结果，没有根据“稀疏”标志假定为 4 KiB。

完整最终结果：`results/sizes-admin-v2.json`。其中每项均包含 MFT 解析值、Windows 原始查询值、核对依据、稳定性检查和读取/解析耗时。此前未通过的结果保留在 v1，用于追溯口径修正。微量样本耗时不用于推算全盘扫描速度。

## 使用

管理员 PowerShell 中，用真实文件的绝对路径执行；输出必须是新文件：

```powershell
python 'E:\桌面\cleaner\size_demo.py' 'E:\桌面\cleaner\mft_demo.py' --output 'E:\桌面\cleaner\results\my-size-check.json'
```

同时支持多个文件路径。新造隔离样本使用 `make_size_fixtures.py`；它是唯一会创建/设置测试文件的辅助脚本，所有样本位于新建的 `size-check-*` 目录，不覆盖现有文件。size_demo.py 自身不更改源文件、权限、USN 日志或卷结构。

运行解析测试：

```powershell
python -m unittest discover -s 'E:\桌面\cleaner' -p 'test_*.py' -v
```

## 尚未实现

- `$ATTRIBUTE_LIST` 和扩展记录合并，遇到此类文件会明确标为 unsupported/unresolved，不猜大小。
- 目录索引、NTFS 系统元数据和全卷物理占用对账。
- 所有文件的完整索引、全盘目录聚合、硬链接归属策略。
- `$MFT` 区段定位与分块读取。当前逐记录接口只用作正确性基准，不能替代后续高速读取主流程。

下一步：让分块读取的 FILE 记录复用 ntfs_record.py，在明确 raw 来源和校验的前提下处理 MFT 碎片、扩展属性，并与本 demo 的正确性结果比较。

## 依据

- https://learn.microsoft.com/en-us/windows/win32/api/winioctl/ni-winioctl-fsctl_get_ntfs_file_record
- https://learn.microsoft.com/en-us/windows/win32/devnotes/file-record-segment-header
- https://learn.microsoft.com/en-us/windows/win32/devnotes/attribute-record-header
- https://learn.microsoft.com/en-us/windows/win32/api/winbase/ns-winbase-file_standard_info
- https://learn.microsoft.com/en-us/windows/win32/api/winbase/ns-winbase-file_compression_info
