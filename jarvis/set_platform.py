import json, os

settings_path = os.path.expanduser("~/.vscode-server/data/Machine/settings.json")
os.makedirs(os.path.dirname(settings_path), exist_ok=True)

try:
    with open(settings_path) as f:
        settings = json.load(f)
except:
    settings = {}

settings["remote.SSH.remotePlatform"] = {"68.183.107.46": "linux"}

with open(settings_path, "w") as f:
    json.dump(settings, f, indent=2)

print("Done")
