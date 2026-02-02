from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import requests
from packaging.version import Version


class UpdateError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    notes: str
    asset_name: str
    browser_download_url: str
    published_at: str


def _debug_updater_enabled() -> bool:
    val = (os.environ.get("THERMALBENCH_UPDATER_DEBUG") or "").strip().lower()
    return val in {"1", "true", "yes", "on"}


def _debug_print(*parts: object) -> None:
    if not _debug_updater_enabled():
        return
    print(*parts, file=sys.stderr, flush=True)


def _parse_version_tag(tag_name: str) -> str:
    tag = (tag_name or "").strip()
    if tag.startswith("v"):
        tag = tag[1:]
    return tag


def _iter_assets(release_json: dict[str, Any]) -> Iterable[dict[str, Any]]:
    assets = release_json.get("assets")
    if isinstance(assets, list):
        for asset in assets:
            if isinstance(asset, dict):
                yield asset


def _select_installer_asset(
    assets: Iterable[dict[str, Any]],
    version: str,
    installer_prefix: str,
) -> tuple[str, str]:
    expected = f"{installer_prefix}{version}.exe"

    found_prefix_match: tuple[str, str] | None = None

    for asset in assets:
        name = str(asset.get("name") or "")
        url = str(asset.get("browser_download_url") or "")
        if not name or not url:
            continue

        if name == expected:
            return name, url

        if name.startswith(installer_prefix) and name.lower().endswith(".exe"):
            # fallback: any installer matching prefix + .exe
            if found_prefix_match is None:
                found_prefix_match = (name, url)

    if found_prefix_match is not None:
        return found_prefix_match

    raise UpdateError(
        f"No installer asset found. Expected '{expected}' or any '{installer_prefix}*.exe'."
    )


def fetch_latest_release_info(
    owner: str,
    repo: str,
    *,
    installer_prefix: str = "ThermalBench-Setup-v",
    timeout_s: float = 15.0,
) -> ReleaseInfo:
    url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": f"{repo}-manual-updater",
    }

    try:
        resp = requests.get(url, headers=headers, timeout=timeout_s)
    except requests.RequestException as e:
        raise UpdateError(f"GitHub API request failed: {e}") from e

    _debug_print("Update URL:", url)
    _debug_print("Status:", resp.status_code)
    try:
        _debug_print("Body:", (resp.text or "")[:300])
    except Exception:
        # Never let debug printing break the updater.
        pass

    if resp.status_code == 403:
        remaining = resp.headers.get("X-RateLimit-Remaining")
        reset = resp.headers.get("X-RateLimit-Reset")
        if remaining == "0":
            raise UpdateError(
                f"GitHub API rate limit exceeded (X-RateLimit-Reset={reset})."
            )
        raise UpdateError(f"GitHub API forbidden (HTTP 403).")

    if resp.status_code == 404:
        raise UpdateError("GitHub repo not found or no releases (HTTP 404).")

    if not resp.ok:
        raise UpdateError(f"GitHub API error: HTTP {resp.status_code}")

    try:
        data = resp.json()
    except ValueError as e:
        raise UpdateError("GitHub API returned invalid JSON.") from e

    tag_name = str(data.get("tag_name") or "")
    version = _parse_version_tag(tag_name)
    if not version:
        raise UpdateError("Latest release missing tag_name.")

    notes = str(data.get("body") or "")
    published_at = str(data.get("published_at") or "")

    asset_name, download_url = _select_installer_asset(
        _iter_assets(data), version=version, installer_prefix=installer_prefix
    )

    return ReleaseInfo(
        version=version,
        notes=notes,
        asset_name=asset_name,
        browser_download_url=download_url,
        published_at=published_at,
    )


def is_newer_version(local_version: str, remote_version: str) -> bool:
    try:
        return Version(remote_version) > Version(local_version)
    except Exception as e:
        raise UpdateError(f"Invalid version format: {e}") from e


def get_updates_dir(app_name: str = "ThermalBench") -> Path:
    localappdata = os.environ.get("LOCALAPPDATA")
    if not localappdata:
        raise UpdateError("%LOCALAPPDATA% is not set; cannot choose updates folder.")
    return Path(localappdata) / app_name / "updates"


def download_release_asset(
    release: ReleaseInfo,
    *,
    app_name: str = "ThermalBench",
    timeout_s: float = 30.0,
    chunk_size: int = 1024 * 1024,
) -> Path:
    if os.name != "nt":
        raise UpdateError("Updater is Windows-only.")

    updates_dir = get_updates_dir(app_name)
    updates_dir.mkdir(parents=True, exist_ok=True)

    final_path = updates_dir / release.asset_name
    partial_path = final_path.with_suffix(final_path.suffix + ".part")

    try:
        with requests.get(release.browser_download_url, stream=True, timeout=timeout_s) as resp:
            _debug_print("Download URL:", release.browser_download_url)
            _debug_print("Status:", resp.status_code)
            if not resp.ok:
                try:
                    _debug_print("Body:", (resp.text or "")[:300])
                except Exception:
                    pass
                raise UpdateError(f"Download failed: HTTP {resp.status_code}")

            with open(partial_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=chunk_size):
                    if chunk:
                        f.write(chunk)

        if final_path.exists():
            try:
                final_path.unlink()
            except Exception:
                pass

        partial_path.replace(final_path)
        return final_path

    except UpdateError:
        # already normalized
        raise
    except requests.RequestException as e:
        raise UpdateError(f"Download request failed: {e}") from e
    except Exception as e:
        raise UpdateError(f"Download failed: {e}") from e
    finally:
        # cleanup partial on any error path
        if partial_path.exists() and not final_path.exists():
            try:
                partial_path.unlink()
            except Exception:
                pass


def launch_installer(installer_path: Path) -> None:
    if os.name != "nt":
        raise UpdateError("Installer launch is Windows-only.")

    if not installer_path.exists():
        raise UpdateError(f"Installer not found: {installer_path}")

    try:
        subprocess.Popen([str(installer_path)])
    except Exception as e:
        raise UpdateError(f"Failed to launch installer: {e}") from e
