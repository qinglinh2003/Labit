When the user says /sync, manage data synchronization between compute node and R2.

## Usage
- `/sync` — show sync status (what's on compute vs R2)
- `/sync push` — compute node → R2 (backup after experiments)
- `/sync pull` — R2 → compute node (restore on new machine)

## Steps

1. Read configs/active_project and load the project config.

2. Get sync_dirs from the project config (list of directory names to sync).

3. Get compute config: host, workdir, and the project repo name.

4. R2 paths follow the convention: `r2:research-data/{project_name}/{dir}`
   Compute paths follow: `{workdir}/{repo_name}/{dir}`
   
   Example for PGOOM with sync_dirs: ["outputs"]:
   - Compute: /workspace/PGOOM/outputs/
   - R2: r2:research-data/PGOOM/outputs/

5. For `/sync` (status):
   - SSH to compute node: `rclone size {compute_path}` for each sync_dir
   - Run locally: `rclone size r2:research-data/{project}/{dir}` for each sync_dir
   - Show a comparison table: dir name, compute size, R2 size

6. For `/sync push`:
   - For each sync_dir, SSH to compute and run:
     `rclone copy {compute_path} r2:research-data/{project}/{dir} --update --transfers 8 --progress`
   - Report what was transferred

7. For `/sync pull`:
   - For each sync_dir, SSH to compute and run:
     `rclone copy r2:research-data/{project}/{dir} {compute_path} --update --transfers 8 --progress`
   - Report what was transferred

Note: Use `rclone copy` (not `rclone sync`) to avoid deleting files that exist on one side but not the other. The `--update` flag skips files that are newer on the destination.
