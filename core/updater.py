from __future__ import annotations

import base64
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

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

    # Only accept installers that match the release version.
    # This avoids accidentally downloading an older installer if the release assets are mis-uploaded.
    candidates: list[str] = []

    for asset in assets:
        name = str(asset.get("name") or "")
        url = str(asset.get("browser_download_url") or "")
        if not name or not url:
            continue

        if name.startswith(installer_prefix) and name.lower().endswith(".exe"):
            candidates.append(name)

        if name == expected:
            return name, url

        # Allow minor filename variations as long as it clearly targets this version.
        # e.g. "ThermalBench-Setup-v0.0.2 (1).exe" or "ThermalBench-Setup-v0.0.2-x64.exe"
        if name.startswith(f"{installer_prefix}{version}") and name.lower().endswith(".exe"):
            return name, url

    if candidates:
        raise UpdateError(
            "No matching installer asset found for this release. "
            f"Expected '{expected}' (or a filename starting with '{installer_prefix}{version}' and ending in '.exe'). "
            f"Found installer assets: {', '.join(candidates)}"
        )

    raise UpdateError(
        "No installer asset found in the release assets. "
        f"Expected '{expected}' (or a filename starting with '{installer_prefix}{version}' and ending in '.exe')."
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

    _debug_print("Tag:", tag_name)
    _debug_print("Parsed version:", version)

    notes = str(data.get("body") or "")
    published_at = str(data.get("published_at") or "")

    asset_name, download_url = _select_installer_asset(
        _iter_assets(data), version=version, installer_prefix=installer_prefix
    )

    _debug_print("Selected asset:", asset_name)

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
    progress_cb: Callable[[int, int | None], None] | None = None,
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

            total: int | None = None
            try:
                cl = resp.headers.get("Content-Length")
                if cl is not None:
                    total = int(cl)
            except Exception:
                total = None

            downloaded = 0
            if progress_cb is not None:
                try:
                    progress_cb(downloaded, total)
                except Exception:
                    pass

            with open(partial_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=chunk_size):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_cb is not None:
                            try:
                                progress_cb(downloaded, total)
                            except Exception:
                                pass

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


def launch_installer(
    installer_path: Path,
    *,
    silent: bool = False,
    extra_args: list[str] | None = None,
) -> None:
    if os.name != "nt":
        raise UpdateError("Installer launch is Windows-only.")

    if not installer_path.exists():
        raise UpdateError(f"Installer not found: {installer_path}")

    args: list[str] = [str(installer_path)]
    if silent:
        # Inno Setup silent install flags
        args += [
            "/VERYSILENT",
            "/SUPPRESSMSGBOXES",
            "/NORESTART",
            "/CLOSEAPPLICATIONS",
            "/RESTARTAPPLICATIONS",
        ]
    if extra_args:
        args += [str(a) for a in extra_args if str(a).strip()]

    try:
        subprocess.Popen(args)
    except Exception as e:
        raise UpdateError(f"Failed to launch installer: {e}") from e


def _ps_single_quote(value: str) -> str:
        # PowerShell single-quoted string escaping: ' becomes ''
        return "'" + (value or "").replace("'", "''") + "'"


def _powershell_encoded_command(script: str) -> str:
        # -EncodedCommand expects UTF-16LE bytes base64-encoded.
        raw = (script or "").encode("utf-16le")
        return base64.b64encode(raw).decode("ascii")


def launch_installer_with_updater_ui(
        installer_path: Path,
        *,
        wait_for_pid: int | None = None,
        silent: bool = True,
        restart_exe: str | None = None,
        restart_args: list[str] | None = None,
) -> None:
        """Launch the installer via a separate process that shows a small UI.

        Why this exists:
        - On Windows, the running app EXE is locked; the app must exit for the installer to replace it.
        - Users shouldn't stare at "nothing" while the installer runs; this shows a small "Updating…" window.

        Implementation detail:
        - Uses PowerShell + WPF to show a tiny progress window.
        - Waits for the calling process PID (ThermalBench) to exit before starting the installer.
        - Restarts the app after installer completion (but avoids double-start if the installer already starts it).
        """

        if os.name != "nt":
                raise UpdateError("Installer launch is Windows-only.")

        if not installer_path.exists():
                raise UpdateError(f"Installer not found: {installer_path}")

        if restart_exe is None:
                if getattr(sys, "frozen", False):
                        restart_exe = sys.executable
                else:
                        # Dev mode: relaunch via the current Python and app.py at repo root.
                        repo_root = Path(__file__).resolve().parents[1]
                        restart_exe = sys.executable
                        restart_args = [str(repo_root / "app.py")]

        restart_args = restart_args or []

        # Derive process name for "already running" detection.
        try:
                proc_name = Path(restart_exe).stem
        except Exception:
                proc_name = "ThermalBench"

        installer_args: list[str] = []
        if silent:
                installer_args += [
                        "/VERYSILENT",
                        "/SUPPRESSMSGBOXES",
                        "/NORESTART",
                ]

        ps_installer = _ps_single_quote(str(installer_path))
        ps_installer_args = ",".join(_ps_single_quote(a) for a in installer_args)
        ps_restart_exe = _ps_single_quote(str(restart_exe))
        ps_restart_args = ",".join(_ps_single_quote(a) for a in restart_args)
        ps_proc_name = _ps_single_quote(proc_name)
        ps_wait_for_pid = "" if wait_for_pid is None else str(int(wait_for_pid))

        ps_template = """
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName PresentationFramework | Out-Null

$win = New-Object System.Windows.Window
$win.Title = 'ThermalBench Update'
$win.Width = 460
$win.Height = 160
$win.WindowStartupLocation = 'CenterScreen'
$win.ResizeMode = 'NoResize'
$win.Topmost = $true

$panel = New-Object System.Windows.Controls.StackPanel
$panel.Margin = '16'

$title = New-Object System.Windows.Controls.TextBlock
$title.FontSize = 16
$title.FontWeight = 'SemiBold'
$title.Text = 'Updating ThermalBench'
$panel.Children.Add($title) | Out-Null

$msg = New-Object System.Windows.Controls.TextBlock
$msg.Margin = '0,10,0,0'
$msg.TextWrapping = 'Wrap'
$panel.Children.Add($msg) | Out-Null

$hint = New-Object System.Windows.Controls.TextBlock
$hint.Margin = '0,10,0,0'
$hint.Opacity = 0.7
$hint.Text = 'Please wait. The app will restart when finished.'
$panel.Children.Add($hint) | Out-Null

$win.Content = $panel

$installer = __INSTALLER__
$installerArgs = @(__INSTALLER_ARGS__)
$restartExe = __RESTART_EXE__
$restartArgs = @(__RESTART_ARGS__)
$procName = __PROC_NAME__
$pidToWaitRaw = __WAIT_FOR_PID__

$msg.Text = 'Closing ThermalBench…'

if ($pidToWaitRaw -ne '') {
    $pidToWait = [int]$pidToWaitRaw
    for ($i = 0; $i -lt 120; $i++) {
        try {
            $p = Get-Process -Id $pidToWait -ErrorAction Stop
            Start-Sleep -Milliseconds 250
        } catch {
            break
        }
    }
}

$msg.Text = 'Installing update…'

$proc = Start-Process -FilePath $installer -ArgumentList $installerArgs -PassThru

$timer = New-Object System.Windows.Threading.DispatcherTimer
$timer.Interval = [TimeSpan]::FromMilliseconds(250)
$timer.Add_Tick({
    if ($proc.HasExited) {
        $timer.Stop()

        if ($proc.ExitCode -ne 0) {
            $msg.Text = "Installer failed (exit code $($proc.ExitCode))."
            $hint.Text = 'Close this window and try again.'
            return
        }

        $msg.Text = 'Restarting…'
        Start-Sleep -Milliseconds 400

        $alreadyRunning = $false
        try {
            $alreadyRunning = (Get-Process -Name $procName -ErrorAction SilentlyContinue) -ne $null
        } catch {
            $alreadyRunning = $false
        }

        if (-not $alreadyRunning) {
            Start-Process -FilePath $restartExe -ArgumentList $restartArgs | Out-Null
        }

        $win.Close()
    }
})
$timer.Start()

$null = $win.ShowDialog()
"""

        ps = (
                ps_template.replace("__INSTALLER__", ps_installer)
                .replace("__INSTALLER_ARGS__", ps_installer_args)
                .replace("__RESTART_EXE__", ps_restart_exe)
                .replace("__RESTART_ARGS__", ps_restart_args)
                .replace("__PROC_NAME__", ps_proc_name)
                .replace("__WAIT_FOR_PID__", _ps_single_quote(ps_wait_for_pid))
        )

        encoded = _powershell_encoded_command(ps)
        cmd = [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-WindowStyle",
                "Hidden",
                "-EncodedCommand",
                encoded,
        ]

        try:
                subprocess.Popen(cmd)
        except Exception as e:
                raise UpdateError(f"Failed to start updater UI: {e}") from e
