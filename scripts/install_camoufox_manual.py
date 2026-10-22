import json

from camoufox.multiversion import BROWSERS_DIR, COMPAT_FLAG, CONFIG_FILE

target = BROWSERS_DIR / "official" / "156.0.1-beta.36"

meta = {
    "version": "156.0.1",
    "build": "beta.36",
    "prerelease": True,
    "sha256": None,
    "created_at": None,
    "interface": 1,
}
(target / "version.json").write_text(json.dumps(meta), encoding="utf-8")

config = {}
if CONFIG_FILE.exists():
    try:
        config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        config = {}
config["active_version"] = "browsers/official/156.0.1-beta.36"
CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
CONFIG_FILE.write_text(json.dumps(config, indent=2), encoding="utf-8")

COMPAT_FLAG.touch()
print("target:", target)
print("config:", CONFIG_FILE.read_text(encoding="utf-8"))
