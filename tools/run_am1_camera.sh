#!/usr/bin/env bash
# Camera-only foreground launcher. Never starts the motor host or opens a serial bus.
set -uo pipefail
umask 077
repository_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)" || exit 2
python="${AM1_CAMERA_PYTHON:-$repository_root/.venv/bin/python}"
viewer="$repository_root/tools/am1_camera_viewer.py"
[[ -x "$python" ]] || { printf 'Set AM1_CAMERA_PYTHON to the existing Pi Python environment.\n' >&2; exit 2; }
for argument in "$@"; do
    case "$argument" in
        --help|-h|--check|--configure|--init-auth)
            # Auth entry is direct/interactive, never captured in a session log.
            exec "$python" "$viewer" "$@"
            ;;
    esac
done
[[ -z "$(git -C "$repository_root" status --porcelain)" ]] || {
    printf 'Camera worktree must be clean before capture.\n' >&2; exit 2;
}
log_directory="${AM1_CAMERA_LOG_DIRECTORY:-$HOME/AlohaMini1Logs}"
mkdir -p -- "$log_directory" || exit 2
log_path="$(mktemp "$log_directory/am1-camera-$(date +%Y%m%d-%H%M%S)-XXXXXX.log")" || exit 2
printf 'CAMERA_LOG=%s\nRuntime output goes directly to disk. Ctrl+C HERE stops the viewer.\n' "$log_path"
{
    printf 'CAMERA_SOURCE_BRANCH=%s\n' "$(git -C "$repository_root" branch --show-current)"
    printf 'CAMERA_SOURCE_HEAD=%s\n' "$(git -C "$repository_root" rev-parse HEAD)"
    printf 'CAMERA_SOURCE_ROOT=%s\n' "$repository_root"
    printf 'CAMERA_PYTHON=%s\n' "$python"
    printf 'CAMERA_BINARY_VERSION=go2rtc-1.9.14-arm64\n'
} >>"$log_path" || exit 2
PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 "$python" "$viewer" "$@" >>"$log_path" 2>&1
camera_exit=$?
printf 'CAMERA_EXIT_CODE=%s\n' "$camera_exit" >>"$log_path" || exit 2
printf 'CAMERA_EXIT_CODE=%s\nCAMERA_LOG=%s\n' "$camera_exit" "$log_path"
exit "$camera_exit"
