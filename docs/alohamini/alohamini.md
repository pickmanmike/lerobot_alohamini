# AlohaMini — Full Workflow

> **Prerequisites:** complete [install.md](install.md) first.  
> **Hardware profiles:** see [profiles.md](profiles.md).

Dual-arm setup — PC (client) + Raspberry Pi (host) on the same LAN.

---

## 1. System Architecture

```
┌──────────────────────────────┐        LAN        ┌──────────────────────────────────┐
│         PC (Client)          │ ◄───────────────► │      Raspberry Pi (Host)         │
│                              │                   │                                  │
│  • Leader arms (USB)         │                   │  • Follower arms (USB)           │
│  • calibrate_bi.py           │                   │  • Base wheels + lift (USB)      │
│  • teleoperate_bi.py         │                   │  • Cameras (USB)                 │
│  • record_bi.py              │                   │  • alohamini_host.py             │
│  • Training / Evaluation     │                   │                                  │
└──────────────────────────────┘                   └──────────────────────────────────┘
```

Both machines must be on the same LAN with the environment required for their host or client role.

---

## 2. Port Configuration

Plug in one device at a time, then run:

```bash
lerobot-find-port
# or check directly:
ls /dev/ttyACM*
```

**Follower arms** — edit `src/lerobot/robots/alohamini/config_alohamini.py` on the Pi:

```python
@dataclass
class AlohaMiniConfig(RobotConfig):
    left_port:  str = "/dev/ttyACM0"   # replace with your left-bus port
    right_port: str = "/dev/ttyACM1"   # replace with your right-bus port
```

**Leader arms** — on Linux, the PC scripts default to the stable device aliases below:

```python
left_arm_config  = SOLeaderConfig(port="/dev/am_arm_leader_left", ...)
right_arm_config = SOLeaderConfig(port="/dev/am_arm_leader_right", ...)
```

Set up the corresponding udev aliases as described in [commands.md](commands.md#persistent-arm-ports). If you use different paths, pass the same `--teleop.left_port` and `--teleop.right_port` values to `calibrate_bi.py`, `teleoperate_bi.py`, and `record_bi.py`.

> Port numbers can change after reconnecting or rebooting. If you purchased a complete AlohaMini, the Pi's follower ports are already fixed via udev rules — no action needed.

### Native Windows leader client (Aloha Mini 1)

Native Windows support is for the PC/client role shown above, not the Raspberry Pi hardware-host role. In PowerShell, create a targeted Python 3.12 environment for leader calibration and network teleoperation:

```powershell
cd C:\Users\pickm\lerobot_alohamini_client
uv venv --python 3.12
uv pip install -e ".[hardware,feetech,pyzmq-dep]"
```

Do not install `.[all]` for this workflow. The initial client environment does not need kinematics, `placo`, `eiquadprog`, `cmeel-boost`, simulation, training policies, Jupyter, phone control, or visualization. Dataset recording can be enabled later with its targeted dataset dependencies.

Find the two leader ports with one USB controller connected at a time, recording which COM port belongs to the left and right leader:

```powershell
.\.venv\Scripts\lerobot-find-port.exe
```

Windows requires both ports explicitly. The examples below use `COM7` for the left leader and `COM8` for the right leader; substitute the ports found on your PC. Calibrate the passive leader pair with the same ID and arm profile that later teleoperation and recording commands will use:

```powershell
.\.venv\Scripts\python.exe `
  .\examples\alohamini\calibrate_bi.py `
  --teleop.left_port COM7 `
  --teleop.right_port COM8 `
  --teleop.id so101_leader_bi `
  --teleop.arm_profile so-arm-5dof
```

Aloha Mini leader and follower arm actions use normalized positions by default: body joints use `-100..100` and grippers use `0..100`. Existing leader calibration files remain reusable because they store raw homing, range, and drive information rather than the runtime normalization mode. Do not recalibrate solely for this change.

### Aloha Mini 1 startup synchronization safety

`strict` remains the default startup mode and never automatically positions followers. `sync` is an Aloha Mini 1-only linear interpolation in normalized joint space: it makes an explicit, slow move from newly measured follower positions to one frozen, validated leader pose. It is not collision-aware and does not check self-collision, the workspace, payloads, cables, or nearby people.

Begin every stage with empty grippers, a clear motion envelope, the passive leaders held in moderate poses, the tested follower supported, and the follower motor-power disconnect immediately accessible. Stop at the first unexpected direction, speed, sound, current, contact, software error, or communication failure. Synchronization does not automatically reverse or return an arm after a refusal, and the Pi may continue holding the last arm target.

Leader motors require their 7.4 V low-voltage supply and must never receive the 12 V follower supply. Physical commissioning is not part of software validation and requires separate authorization; use the stages below only as separately authorized, bounded physical checks. Keep the Pi host's `max_relative_target` as an independently selected secondary limit; this Windows client does not configure it.

Before a synchronization move, the client prints the measured start and frozen target and asks the operator to type exactly `SYNC`. Enter alone, lowercase text, or added whitespace does not authorize motion. After confirmation, the client takes fresh follower and leader samples and prints those final endpoints before sending frame zero. Every synchronization frame holds base and lift velocity at zero and changes each selected normalized arm position by at most `STARTUP_SYNC_MAX_STEP = 0.75`. This client frame cap is independent of Pi `max_relative_target`; if it needs more frames than the requested duration, the move takes longer. Actual arm-bearing synchronization sends remain at least `1 / --fps` seconds apart, so an overrun lengthens the move instead of triggering catch-up sends. Every leader sample is validated, and exceeding `STARTUP_SYNC_LEADER_DRIFT = 2.0` aborts selected-side motion.

Command and observation traffic use separate sockets, so the first sequence-fresh response after the final command can still have been generated before that command was processed. The client therefore checks up to the configured observation request window plus one sequence-fresh samples. Synchronization succeeds only when a checked follower sample satisfies `--max_start_mismatch`; otherwise it refuses without widening the threshold. The threshold is final convergence verification only: it does not limit how far apart valid calibrated poses may be when a synchronization plan is first proposed. Use `5.0` for the remaining AM1 commissioning checks; the parser's `10.0` default remains for compatibility and is not the recommended commissioning value.

The client makes the operator phase explicit, in this order:

1. `HOLD LEADERS STILL — STARTUP SYNCHRONIZATION IN PROGRESS`
2. `SYNCHRONIZATION COMPLETE`
3. `PRESS ENTER TO ENABLE LIVE TELEOPERATION`
4. `TELEOPERATION ACTIVE — LEADER MOVEMENT IS NOW ALLOWED`

The final message appears only after the post-pause fresh-sample alignment gate passes and immediately before the first ordinary arm action is sent.

#### Bounded AM1 single-joint diagnostic

If synchronization cannot distinguish limited positional headroom from a joint-specific powered fault, stop ordinary commissioning and use the network-only AM1 diagnostic below in a separately authorized powered session. It constructs no leader, keyboard, camera, or visualization device. It takes a fresh follower pose, holds all nonselected joints at the last pre-move measured pose, sends frame zero at that pose, keeps base and lift commands explicitly zero, and exits after one bounded ramp. No arm-bearing command is sent unless the operator types exact uppercase `MOVE`.

```powershell
.\.venv\Scripts\python.exe `
  .\examples\alohamini\diagnose_am1_joint.py `
  --robot.remote_ip 192.168.1.134 `
  --robot.id my_alohamini `
  --side left `
  --joint elbow_flex `
  --delta -10.0 `
  --fps 5 `
  --duration_s 5.0 `
  --max_final_error 1.0
```

The client can report what it sent and what it subsequently observed, but the current action socket supplies no host-acceptance acknowledgement. `PASS` therefore means the sequence-fresh observed position reached the requested tolerance; it does not prove a persistent host setting. In the observed left-elbow case, the Pi's `max_relative_target=5.0` produced an exact clamp from approximately `99.909` to `94.909` while the joint stayed at approximately `99.909`. A separately authorized repeat with only `max_relative_target=10.0` changed is the next hypothesis test. It is not a proven production value, and the client step cap, leader-drift limit, and recommended `5.0` final convergence tolerance must not be widened.

After a run, fetch the newest Pi log from Windows with:

```powershell
.\tools\fetch_am1_pi_log.ps1
```

The helper defaults to `pickmanmike@192.168.1.134`, prints the exact remote and local paths, and saves into `$HOME\AlohaMini1Logs`. To retrieve a known log instead of discovering the newest one:

```powershell
.\tools\fetch_am1_pi_log.ps1 `
  -RemotePath /home/pickmanmike/am1-left-elbow-diagnostic-YYYY-MM-DD-HHMMSS.log
```

Run S1 through S6 in order. Stop after each stage and review the observed movement and cleanup before authorizing the next stage.

#### S1 — left-only synchronization and exit

```powershell
.\.venv\Scripts\python.exe `
  .\examples\alohamini\teleoperate_bi.py `
  --robot.remote_ip 192.168.1.134 `
  --robot.robot_model alohamini1 `
  --teleop.left_port COM7 `
  --teleop.right_port COM8 `
  --teleop.id so101_leader_bi `
  --teleop.arm_profile so-arm-5dof `
  --startup_mode sync `
  --startup_sync_side left `
  --startup_sync_duration_s 12.0 `
  --startup_sync_only `
  --max_start_mismatch 5.0 `
  --fps 5 `
  --no_keyboard `
  --no_rerun
```

S1 sends only left-arm position keys plus zero base/lift commands, verifies the result, and exits without entering ordinary teleoperation.

#### S2 — right-only synchronization and exit

```powershell
.\.venv\Scripts\python.exe `
  .\examples\alohamini\teleoperate_bi.py `
  --robot.remote_ip 192.168.1.134 `
  --robot.robot_model alohamini1 `
  --teleop.left_port COM7 `
  --teleop.right_port COM8 `
  --teleop.id so101_leader_bi `
  --teleop.arm_profile so-arm-5dof `
  --startup_mode sync `
  --startup_sync_side right `
  --startup_sync_duration_s 12.0 `
  --startup_sync_only `
  --max_start_mismatch 5.0 `
  --fps 5 `
  --no_keyboard `
  --no_rerun
```

S2 applies the same bounded procedure to the right follower only.

#### S3 — strict no-motion alignment diagnostic

Manually place and support both followers in poses matching the passive leaders, then run the existing strict diagnostic:

```powershell
.\.venv\Scripts\python.exe `
  .\examples\alohamini\teleoperate_bi.py `
  --robot.remote_ip 192.168.1.134 `
  --robot.robot_model alohamini1 `
  --teleop.left_port COM7 `
  --teleop.right_port COM8 `
  --teleop.id so101_leader_bi `
  --teleop.arm_profile so-arm-5dof `
  --startup_mode strict `
  --check_alignment_only `
  --max_start_mismatch 5.0 `
  --no_keyboard `
  --no_rerun
```

S3 prints every follower/leader joint difference and sends no arm-position action. It succeeds only when both samples are finite, normalized, complete, fresh, and within the threshold. A base/lift zero command may still be sent during connection and cleanup.

#### S4 — both-side synchronization and exit

```powershell
.\.venv\Scripts\python.exe `
  .\examples\alohamini\teleoperate_bi.py `
  --robot.remote_ip 192.168.1.134 `
  --robot.robot_model alohamini1 `
  --teleop.left_port COM7 `
  --teleop.right_port COM8 `
  --teleop.id so101_leader_bi `
  --teleop.arm_profile so-arm-5dof `
  --startup_mode sync `
  --startup_sync_side both `
  --startup_sync_duration_s 15.0 `
  --startup_sync_only `
  --max_start_mismatch 5.0 `
  --fps 5 `
  --no_keyboard `
  --no_rerun
```

#### S5 — strict bounded gripper-only operator procedure

This is an operator procedure, not a gripper-only payload mode. The client still validates and forwards all twelve arm-position keys; during this check the operator moves only the two grippers.

```powershell
.\.venv\Scripts\python.exe `
  .\examples\alohamini\teleoperate_bi.py `
  --robot.remote_ip 192.168.1.134 `
  --robot.robot_model alohamini1 `
  --teleop.left_port COM7 `
  --teleop.right_port COM8 `
  --teleop.id so101_leader_bi `
  --teleop.arm_profile so-arm-5dof `
  --startup_mode strict `
  --max_start_mismatch 5.0 `
  --fps 5 `
  --duration_s 30 `
  --start_paused `
  --no_keyboard `
  --no_rerun
```

After Enter, the client obtains fresh follower and leader samples, revalidates and compares them, and uses the final validated leader sample as the first forwarded arm action with explicit zero base/lift commands.

#### S6 — both-side synchronization followed by paused teleoperation

```powershell
.\.venv\Scripts\python.exe `
  .\examples\alohamini\teleoperate_bi.py `
  --robot.remote_ip 192.168.1.134 `
  --robot.robot_model alohamini1 `
  --teleop.left_port COM7 `
  --teleop.right_port COM8 `
  --teleop.id so101_leader_bi `
  --teleop.arm_profile so-arm-5dof `
  --startup_mode sync `
  --startup_sync_side both `
  --startup_sync_duration_s 15.0 `
  --max_start_mismatch 5.0 `
  --fps 5 `
  --duration_s 60 `
  --start_paused `
  --no_keyboard `
  --no_rerun
```

Synchronization verifies the frozen target before the pause. After Enter, the client again requires a fresh follower observation proven by sequence advancement and a fresh normalized leader sample. It revalidates and re-compares both sides, then forwards that final validated leader sample first with zero base/lift commands. The ordinary `--duration_s` clock starts only after synchronization, optional resource setup, and this pause gate.

## 3. Camera Configuration

```bash
lerobot-find-cameras
```

Fill the detected index into `src/lerobot/robots/alohamini/config_alohamini.py`.

> Each camera requires its own USB port — do not share a USB hub between multiple cameras.

---

## 4. Calibration

### Step 1 — Calibrate follower arms (Pi side)

SSH into the Pi and run the calibration script for your model: position each joint at its mechanical midpoint → Enter → rotate 90° left → Enter → rotate 90° right → Enter.

```bash
# AlohaMini 1 (SO-ARM 5-DoF)
python -m lerobot.robots.alohamini.alohamini_calibrate --robot_model alohamini1

# AlohaMini 2 (AM-ARM 6-DoF)
python -m lerobot.robots.alohamini.alohamini_calibrate --robot_model alohamini2

# AlohaMini 2 Pro (AM-ARM 6-DoF, STS3250)
python -m lerobot.robots.alohamini.alohamini_calibrate --robot_model alohamini2pro
```

Starting the host also checks calibration and will prompt this flow automatically if calibration is missing.

SO-ARM 5-DoF reference middle position:

![Calibration SO-ARM](../../examples/alohamini/media/mid_position_so100.png)

### Step 2 — Calibrate leader arms (PC side)

This step connects only to the two leader arms, so the Pi host does not need to be running.

SO-ARM leader (5-DoF):

```bash
python examples/alohamini/calibrate_bi.py \
  --teleop.id so101_leader_bi \
  --teleop.arm_profile so-arm-5dof
```

AM-ARM leader (6-DoF):

```bash
python examples/alohamini/calibrate_bi.py \
  --teleop.id am_leader_bi \
  --teleop.arm_profile am-leader-6dof
```

Use the same `--teleop.id` and `--teleop.arm_profile` for later teleoperation and recording commands so they load the calibration files created here. If a calibration file already exists, press Enter to reuse it or enter `c` to recalibrate.

Running this standalone step is recommended but optional. If it is skipped and no valid calibration is found, `teleoperate_bi.py` keeps the existing behavior: it prompts the user and enters the calibration flow automatically.

> Power-cycle both leader and follower arms after calibration for changes to take effect.

---

## 5. Teleoperation

Start the Pi host first, then the PC client. A valid leader calibration is loaded automatically; if it is missing, the client prompts for calibration before teleoperation starts:

```bash
# Pi — run the host for your robot:
python -m lerobot.robots.alohamini.alohamini_host --robot_model alohamini1
python -m lerobot.robots.alohamini.alohamini_host --robot_model alohamini2
python -m lerobot.robots.alohamini.alohamini_host --robot_model alohamini2pro

# PC — run the client for your leader arm:
python examples/alohamini/teleoperate_bi.py \
  --robot.remote_ip <Pi_IP> \
  --robot.robot_model alohamini1 \
  --teleop.id so101_leader_bi \
  --teleop.arm_profile so-arm-5dof

python examples/alohamini/teleoperate_bi.py \
  --robot.remote_ip <Pi_IP> \
  --robot.robot_model alohamini2 \
  --teleop.id am_leader_bi \
  --teleop.arm_profile am-leader-6dof
```

---

## 6. Dataset Recording

> Make sure the Pi host is already running (§5) before recording.  
> `--teleop.arm_profile` here refers to your **leader arm** hardware, not the follower robot.  
> `--robot.robot_model` must match the model running on the Pi host.  
> Replace `<Pi_IP>` with your Raspberry Pi's IP address.
> `record_bi.py` prints the local dataset path and uploads to Hugging Face Hub by default. Add `--dataset.push_to_hub=false` to keep the dataset local only.
> Add `--dataset.root /path/to/dataset` when you want to store or resume from a specific local directory.

### AlohaMini 1 — SO-ARM leader (5-DoF)

Create new dataset:

```bash
python examples/alohamini/record_bi.py \
  --dataset.repo_id $HF_USER/so100_bi_test \
  --dataset.num_episodes 1 \
  --dataset.fps 30 \
  --dataset.episode_time_s 45 \
  --dataset.reset_time_s 8 \
  --dataset.single_task "pickup1" \
  --robot.remote_ip <Pi_IP> \
  --robot.robot_model alohamini1 \
  --teleop.id so101_leader_bi \
  --teleop.arm_profile so-arm-5dof
```

Resume existing dataset (add `--resume`):

```bash
python examples/alohamini/record_bi.py \
  --dataset.repo_id $HF_USER/so100_bi_test \
  --dataset.num_episodes 1 \
  --dataset.fps 30 \
  --dataset.episode_time_s 45 \
  --dataset.reset_time_s 8 \
  --dataset.single_task "pickup1" \
  --robot.remote_ip <Pi_IP> \
  --robot.robot_model alohamini1 \
  --teleop.id so101_leader_bi \
  --teleop.arm_profile so-arm-5dof \
  --resume
```

### AlohaMini 2 / 2 Pro — AM-ARM leader (6-DoF)

Create new dataset:

```bash
python examples/alohamini/record_bi.py \
  --dataset.repo_id $HF_USER/am2_bi_test \
  --dataset.num_episodes 1 \
  --dataset.fps 30 \
  --dataset.episode_time_s 45 \
  --dataset.reset_time_s 8 \
  --dataset.single_task "pickup1" \
  --robot.remote_ip <Pi_IP> \
  --robot.robot_model alohamini2 \
  --teleop.id am_leader_bi \
  --teleop.arm_profile am-leader-6dof
```

Resume existing dataset (add `--resume`):

```bash
python examples/alohamini/record_bi.py \
  --dataset.repo_id $HF_USER/am2_bi_test \
  --dataset.num_episodes 1 \
  --dataset.fps 30 \
  --dataset.episode_time_s 45 \
  --dataset.reset_time_s 8 \
  --dataset.single_task "pickup1" \
  --robot.remote_ip <Pi_IP> \
  --robot.robot_model alohamini2 \
  --teleop.id am_leader_bi \
  --teleop.arm_profile am-leader-6dof \
  --resume
```

---

## 7. Dataset Replay

```bash
python examples/alohamini/replay_bi.py \
  --dataset.repo_id $HF_USER/am2_bi_test \
  --dataset.episode 0 \
  --robot.remote_ip <Pi_IP> \
  --robot.robot_model alohamini2
```

If the dataset is not under `$HF_LEROBOT_HOME/$HF_USER/am2_bi_test`, add `--dataset.root /path/to/am2_bi_test`.

---

## 8. Dataset Visualization

```bash
lerobot-dataset-viz \
  --repo-id $HF_USER/am2_bi_test \
  --episode-index 0 \
  --display-compressed-images
```

---

## 9. Training

### Local training

```bash
lerobot-train \
  --dataset.repo_id=$HF_USER/am2_bi_test \
  --policy.type=act \
  --output_dir=outputs/train/act_your_dataset1 \
  --job_name=act_your_dataset \
  --policy.device=cuda \
  --wandb.enable=false \
  --policy.repo_id=$HF_USER/act_policy \
  --dataset.video_backend=pyav
```

### No local GPU?

Use any cloud GPU provider (e.g. AutoDL, Lambda Labs, Vast.ai). Set up the environment the same way as local, run the same training command, then copy the checkpoint back to your machine for evaluation.

---

## 10. Evaluation

Make sure the Pi host is already running (§5), then run inference from the PC.

> `--robot.robot_model` must match the model running on the Pi host:  
> `alohamini1` (SO-ARM 5-DoF, 16-dim state) · `alohamini2` / `alohamini2pro` (AM-ARM 6-DoF, 18-dim state)

### `evaluate_bi.py` (custom script, N episodes)

ACT uses synchronous inference. The interpolation multiplier below runs the robot control loop at
`fps × multiplier` (20 × 3 = 60 Hz after the first action) and linearly interpolates between policy actions.

```bash
python examples/alohamini/evaluate_bi.py \
  --eval.n_episodes 3 \
  --fps 20 \
  --eval.episode_time_s 45 \
  --dataset.single_task "Pick and place task" \
  --policy.path outputs/train/act_your_dataset1/checkpoints/020000/pretrained_model \
  --dataset.repo_id $HF_USER/eval_act_policy \
  --dataset.push_to_hub=false \
  --robot.remote_ip <Pi_IP> \
  --robot.id my_alohamini \
  --robot.robot_model alohamini2 \
  --inference.type sync \
  --interpolation_multiplier 3
```

SmolVLA supports Real-Time Chunking (RTC), which runs policy inference asynchronously and refreshes
part of the action chunk while the robot executes queued actions:

```bash
python examples/alohamini/evaluate_bi.py \
  --eval.n_episodes 3 \
  --fps 20 \
  --eval.episode_time_s 45 \
  --dataset.single_task "Pick and place task" \
  --policy.path outputs/train/smolvla_your_dataset1/checkpoints/020000/pretrained_model \
  --dataset.repo_id $HF_USER/eval_smolvla_policy \
  --dataset.push_to_hub=false \
  --robot.remote_ip <Pi_IP> \
  --robot.id my_alohamini \
  --robot.robot_model alohamini2 \
  --inference.type rtc \
  --inference.rtc.execution_horizon 10 \
  --inference.rtc.max_guidance_weight 10.0 \
  --inference.rtc.queue_threshold 30 \
  --interpolation_multiplier 1
```

> Both examples load a local checkpoint and save the evaluation dataset locally without uploading it.
> Make sure the `--policy.path` directory exists and contains the complete pretrained model. Set
> `HF_USER` before running (or replace `$HF_USER` with your username), and use a new
> `--dataset.repo_id` for every evaluation because its local output directory must not already exist.
> ACT does not support RTC; keep `--inference.type sync` for ACT. Replace `<Pi_IP>` with the Pi's IP,
> and change `--robot.robot_model` if the Pi host is running `alohamini1` or `alohamini2pro`.

---

## 11. Debug

See [Debug Command Summary](../../examples/debug/README.md) for the full list of debugging utilities.
