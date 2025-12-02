# core/workflows.py
import json
import subprocess
from pathlib import Path

def run_workflow_json(json_path):
    """
    workflow example: list of steps:
    [
      {"tool":"gdalwarp","args":["-t_srs","EPSG:4326","in.tif","out.tif"]},
      {"tool":"python","script":"scripts/myscript.py","args":["param1"]}
    ]
    """
    j = json.load(open(json_path))
    for step in j:
        tool = step.get("tool")
        if tool == "gdalwarp":
            cmd = ["gdalwarp"] + step.get("args", [])
            subprocess.run(cmd, check=True)
        elif tool == "python":
            cmd = ["python", step.get("script")] + step.get("args", [])
            subprocess.run(cmd, check=True)
        elif tool == "grass":
            # use grass iface - simplified
            cmd = ["grass"] + step.get("args", [])
            subprocess.run(cmd, check=True)
        else:
            raise ValueError("Unknown tool in workflow: " + str(tool))
