import os
import filecmp

dir1 = 'c:/flutter_projects/energy_management_system/lib/rt-dems-main'
dir2 = 'c:/flutter_projects/energy_management_system/lib/rt-dems-final'
out_file = 'c:/flutter_projects/energy_management_system/diff_results.txt'

def compare_dirs(d1, d2, path='', f=None):
    dcmp = filecmp.dircmp(d1, d2, ignore=['__pycache__', 'venv', '.idea', 'db.sqlite3', '.git', 'migrations'])
    for name in dcmp.diff_files:
        f.write(f"MODIFIED: {os.path.join(path, name)}\n")
    for name in dcmp.left_only:
        f.write(f"ONLY IN MAIN: {os.path.join(path, name)}\n")
    for name in dcmp.right_only:
        f.write(f"ONLY IN FINAL: {os.path.join(path, name)}\n")
    for sub in dcmp.subdirs:
        compare_dirs(dcmp.subdirs[sub].left, dcmp.subdirs[sub].right, os.path.join(path, sub), f)

with open(out_file, 'w', encoding='utf-8') as f:
    compare_dirs(dir1, dir2, '', f)
