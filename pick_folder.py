"""Show a native folder dialog only; never scan or modify the selected folder."""
import argparse
import json
from pathlib import Path
import tkinter as tk
from tkinter import filedialog

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    root=tk.Tk()
    root.withdraw()
    root.attributes('-topmost',True)
    try:
        selected=filedialog.askdirectory(parent=root,title='选择要分析的文件夹（只读扫描）',mustexist=True)
        args.output.write_text(json.dumps({'path':selected or None},ensure_ascii=True),encoding='utf-8')
    finally:
        root.destroy()
