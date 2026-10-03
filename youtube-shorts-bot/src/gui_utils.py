"""app.py (Streamlit arayüzü) için yardımcı fonksiyonlar: .env okuma/yazma, ASS<->hex renk
dönüşümü, config.yaml yükleme/kaydetme."""

import os

import yaml


def read_env_value(env_path: str, key: str) -> str:
    if not os.path.exists(env_path):
        return ""
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith(f"{key}="):
                return line[len(key) + 1 :]
    return ""


def write_env_value(env_path: str, key: str, value: str) -> None:
    lines = []
    found = False
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith(f"{key}="):
                    lines.append(f"{key}={value}\n")
                    found = True
                else:
                    lines.append(line if line.endswith("\n") else line + "\n")
    if not found:
        lines.append(f"{key}={value}\n")
    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(lines)


def ass_to_hex(ass_color: str) -> str:
    """&HAABBGGRR (ya da &HBBGGRR) -> #RRGGBB"""
    core = ass_color.replace("&H", "").replace("&h", "").replace("&", "")
    if len(core) == 8:
        core = core[2:]
    core = core.zfill(6)
    bb, gg, rr = core[0:2], core[2:4], core[4:6]
    return f"#{rr}{gg}{bb}"


def hex_to_ass(hex_color: str, alpha: str = "00") -> str:
    """#RRGGBB -> &HAABBGGRR"""
    hex_color = hex_color.lstrip("#")
    rr, gg, bb = hex_color[0:2], hex_color[2:4], hex_color[4:6]
    return f"&H{alpha}{bb}{gg}{rr}"


def load_yaml_config(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def save_yaml_config(path: str, config: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)
