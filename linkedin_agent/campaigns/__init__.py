"""Campaign YAML loading and lookup."""

from __future__ import annotations

import shutil
from pathlib import Path

import yaml
from pydantic import ValidationError

from ..config import Settings
from ..models import Campaign

BUILTIN_DIR = Path(__file__).parent


class CampaignError(ValueError):
    pass


def builtin_campaigns() -> list[Path]:
    return sorted(BUILTIN_DIR.glob("*.yaml"))


def load_campaign(path: Path) -> Campaign:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise CampaignError(f"{path}: invalid YAML: {e}") from e
    if not isinstance(data, dict):
        raise CampaignError(f"{path}: top level must be a mapping")
    try:
        return Campaign.model_validate(data)
    except ValidationError as e:
        msgs = "; ".join(
            f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors()
        )
        raise CampaignError(f"{path}: {msgs}") from e


def resolve_campaign_path(name_or_path: str, settings: Settings) -> Path:
    p = Path(name_or_path).expanduser()
    if p.suffix in (".yaml", ".yml") and p.exists():
        return p
    for candidate in (
        settings.campaigns_dir / f"{name_or_path}.yaml",
        settings.campaigns_dir / f"{name_or_path}.yml",
        BUILTIN_DIR / f"{name_or_path}.yaml",
    ):
        if candidate.exists():
            return candidate
    raise CampaignError(
        f"Campaign {name_or_path!r} not found (looked in {settings.campaigns_dir} and built-ins)"
    )


def find_campaign(name_or_path: str, settings: Settings) -> Campaign:
    return load_campaign(resolve_campaign_path(name_or_path, settings))


def load_all_user_campaigns(settings: Settings) -> dict[str, Campaign]:
    out: dict[str, Campaign] = {}
    if settings.campaigns_dir.exists():
        for path in sorted(settings.campaigns_dir.glob("*.y*ml")):
            try:
                c = load_campaign(path)
            except CampaignError:
                continue
            out[c.name] = c
    return out


def new_campaign_file(name: str, settings: Settings, template: str = "default") -> Path:
    src = resolve_campaign_path(template, settings)
    settings.campaigns_dir.mkdir(parents=True, exist_ok=True)
    dst = settings.campaigns_dir / f"{name}.yaml"
    if dst.exists():
        raise CampaignError(f"{dst} already exists")
    shutil.copy(src, dst)
    text = dst.read_text(encoding="utf-8").replace(f"name: {template}\n", f"name: {name}\n", 1)
    dst.write_text(text, encoding="utf-8")
    return dst
