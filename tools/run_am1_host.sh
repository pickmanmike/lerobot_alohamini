#!/usr/bin/env bash

# Copyright 2026 The HuggingFace Inc. team. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

set -uo pipefail

readonly AM1_WINDOWS_INPUT="30609a4597b8b6fca49bc1018024fd29dfb55127"
readonly AM1_PI_INPUT="ee3a6f5dd813be82780a6a9b1789966357542d2f"

usage() {
    printf '%s\n' \
        'Usage: ./tools/run_am1_host.sh --mode arms|base [--print-command]' \
        '' \
        'Modes:' \
        '  arms  Start the physically validated AM1 arms host (no cameras, unhomed lift).' \
        '  base  Start only the AM1 left body bus for a bounded base test (no follower arms).' \
        '' \
        '--print-command prints the Python command without checking devices or starting the host.'
}

die() {
    printf 'AM1 host launch refused: %s\n' "$*" >&2
    return 2
}

quote_command() {
    printf '%q ' "$@"
    printf '\n'
}

mode=""
print_command=false
while (($#)); do
    case "$1" in
        --mode)
            (($# >= 2)) || { die "--mode requires arms or base"; exit $?; }
            mode="$2"
            shift 2
            ;;
        --print-command)
            print_command=true
            shift
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            die "unknown argument: $1"
            exit $?
            ;;
    esac
done

case "$mode" in
    arms|base) ;;
    *)
        die "--mode must be arms or base"
        exit $?
        ;;
esac

script_path="${BASH_SOURCE[0]//\\//}"
script_parent="${script_path%/*}"
[[ "$script_parent" != "$script_path" ]] || script_parent="."
script_dir="$(cd -- "$script_parent" && pwd -P)"
repository_root="$(cd -- "$script_dir/.." && pwd -P)"
python_path="$repository_root/.venv/bin/python"

command=(
    "$python_path"
    -m lerobot.robots.alohamini.alohamini_host
    --robot_model alohamini1
    --no_cameras
    --skip_lift_home
)
if [[ "$mode" == "arms" ]]; then
    command+=(
        --max_relative_target 20.0
        --max_loop_freq_hz 30
        --profile_timing true
        --profile_cadence
    )
else
    command+=(
        --no_follower
        --max_loop_freq_hz 30
        --profile_timing true
        --profile_cadence
    )
fi

if [[ "$print_command" == true ]]; then
    quote_command "${command[@]}"
    exit 0
fi

cd -- "$repository_root" || exit 2
[[ -x "$python_path" ]] || { die "repository virtualenv Python is missing: $python_path"; exit $?; }
[[ -z "$(git status --porcelain)" ]] || { die "repository worktree is not clean"; exit $?; }
git merge-base --is-ancestor "$AM1_WINDOWS_INPUT" HEAD || {
    die "validated Windows input $AM1_WINDOWS_INPUT is not an ancestor of HEAD"
    exit $?
}
git merge-base --is-ancestor "$AM1_PI_INPUT" HEAD || {
    die "validated Pi input $AM1_PI_INPUT is not an ancestor of HEAD"
    exit $?
}

expected_import="$repository_root/src/lerobot/robots/alohamini/config_alohamini.py"
actual_import="$(
    PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$repository_root/src" "$python_path" -c \
        'from pathlib import Path; import lerobot.robots.alohamini.config_alohamini as m; print(Path(m.__file__).resolve())'
)" || { die "unable to verify the Python import root"; exit $?; }
[[ "$actual_import" == "$expected_import" ]] || {
    die "wrong Python import root: $actual_import"
    exit $?
}

[[ -e /dev/am_arm_follower_left ]] || {
    die "required left body/follower bus alias is absent: /dev/am_arm_follower_left"
    exit $?
}
if [[ "$mode" == "arms" ]]; then
    [[ -e /dev/am_arm_follower_right ]] || {
        die "required right follower bus alias is absent: /dev/am_arm_follower_right"
        exit $?
    }
fi

log_directory="${AM1_LOG_DIRECTORY:-$HOME/AlohaMini1Logs}"
mkdir -p -- "$log_directory" || exit 2
timestamp="$(date +%Y%m%d-%H%M%S)"
log_path="$log_directory/am1-${mode}-host-$timestamp.log"
printf 'HOST_LOG=%s\n' "$log_path" | tee "$log_path" || {
    die "unable to create host log: $log_path"
    exit $?
}
{
    printf 'HOST_COMMAND='
    quote_command "${command[@]}"
} | tee -a "$log_path" || {
    die "unable to record the host command: $log_path"
    exit $?
}

set +e
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$repository_root/src" "${command[@]}" 2>&1 | tee -a "$log_path"
pipeline_status=("${PIPESTATUS[@]}")
host_exit=${pipeline_status[0]}
log_exit=${pipeline_status[1]}
printf 'HOST_EXIT_CODE=%s\n' "$host_exit" | tee -a "$log_path"
exit_log_exit=${PIPESTATUS[1]}
set -e
if ((host_exit != 0)); then
    exit "$host_exit"
fi
if ((log_exit != 0 || exit_log_exit != 0)); then
    die "host exited cleanly, but its log could not be completed: $log_path"
    exit $?
fi
exit "$host_exit"
