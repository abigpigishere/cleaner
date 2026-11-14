"""Read/import an agent advice plan for a scan; never processes source files."""
import argparse,json
from pathlib import Path
from view_scan import Viewer
from cleanup_plan import PlanStore

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--scan-dir',required=True,type=Path);p.add_argument('--import-plan',type=Path);a=p.parse_args()
 v=Viewer(a.scan_dir.resolve())
 try:
  store=PlanStore(v)
  if a.import_plan:
   if a.import_plan.stat().st_size>2*1024*1024:raise ValueError('方案文件过大')
   store.save(json.loads(a.import_plan.read_text(encoding='utf-8-sig')))
  plan,revision=store.load();print(json.dumps({**plan},ensure_ascii=True,indent=2))
 finally:v.index.close()
if __name__=='__main__':main()
