#!/usr/bin/env python3
import os
import sys
from pathlib import Path

# Add the local python_libs to sys.path
repo_root = Path(__file__).resolve().parents[2]
lib_path = repo_root / "python_libs"
sys.path.insert(0, str(lib_path))

try:
    from neuprint import Client, fetch_roi_mesh
except ImportError:
    print("neuprint-python not found in python_libs. Run the installation step first.")
    sys.exit(1)

def main():
    token = os.getenv("NEUPRINT_TOKEN")
    if not token:
        # Check .env
        env_path = repo_root / "backend" / ".env"
        if env_path.exists():
            with open(env_path) as f:
                for line in f:
                    if line.startswith("NEUPRINT_TOKEN="):
                        token = line.strip().split("=", 1)[1]
                        break
    
    if not token:
        print("NEUPRINT_TOKEN not found.")
        sys.exit(1)

    client = Client("https://neuprint.janelia.org", dataset="hemibrain:v1.2.1", token=token)
    
    # Major ROIs to make it look like a brain
    rois = ["EB", "FB", "PB", "AL(L)", "AL(R)", "MB(L)", "MB(R)", "hemibrain"]
    
    out_dir = repo_root / "frontend" / "meshes"
    out_dir.mkdir(parents=True, exist_ok=True)
    
    for roi in rois:
        print(f"Downloading mesh for {roi}...")
        try:
            # fetch_roi_mesh returns an OBJ string
            obj_data = fetch_roi_mesh(roi, client=client)
            # Clean name for filename
            clean_name = roi.replace("(", "_").replace(")", "_")
            out_file = out_dir / f"{clean_name}.obj"
            with open(out_file, "w") as f:
                f.write(obj_data)
            print(f"Saved to {out_file}")
        except Exception as e:
            print(f"Failed to download {roi}: {e}")

if __name__ == "__main__":
    main()
