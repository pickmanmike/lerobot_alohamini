# Motor debug utilities

## Preferred Aloha Mini 1 SSH bring-up

Use `alohamini1_body_smoke.py` for the first Aloha Mini 1 body-bus checks over SSH. It does not
need `pynput` or a graphical session, bounds every motion, verifies acknowledgement-sensitive
writes by readback, checks current during motion, and always attempts zero velocity, torque-off,
and serial close in `finally`.

Start with the zero-motion controller probe:

```bash
python examples/debug/alohamini1_body_smoke.py \
  --port /dev/am_arm_follower_left \
  --action probe-wheel8
```

Then select exactly one bounded action, for example:

```bash
python examples/debug/alohamini1_body_smoke.py \
  --port /dev/am_arm_follower_left \
  --action forward \
  --duration 0.20
```

Run it only when the chassis is supported, moving parts are clear, and the 12 V motor-power
disconnect is reachable. The Aloha Mini 1 mapping used by the helper is ID 8 left wheel, ID 9
rear/back wheel, ID 10 right wheel, and ID 11 lift.

For the explicit Aloha Mini 1 lift commissioning step, use:

```bash
python -m lerobot.robots.alohamini.alohamini_lift_home \
  --robot_model alohamini1 \
  --no_cameras \
  --speed_raw 200 \
  --timeout_s 20
```

The lift zero is process-local. Normal host startup homes the lift again; starting the host with
`--skip_lift_home` deliberately leaves ordinary lift motion disabled.

## Legacy interactive helpers

`wheels.py` and `axis.py` are retained as interactive development tools, but they are not the
preferred first SSH test. Their older initialization and cleanup paths do not provide the same
bounded, readback-verified safety guarantees as `alohamini1_body_smoke.py`.

## General motor inspection and programming

View motor states:

```bash
python examples/debug/motors.py get_motors_states \
  --port /dev/ttyACM0
```

Rotate a specific motor by ID:

```bash
python examples/debug/motors.py move_motor_to_position \
  --id 1 \
  --position 2 \
  --port /dev/ttyACM0
```

Set a new motor ID:

```bash
python examples/debug/motors.py configure_motor_id \
  --id 1 \
  --set_id 8 \
  --port /dev/ttyACM0
```

Set the phase of a specified servo:

```bash
python examples/debug/motors.py configure_motor_phase \
  --id 1 \
  --set_phase 12 \
  --port /dev/ttyACM0
```

Set the phase for all servos:

```bash
python examples/debug/motors.py configure_motor_phase \
  --set_phase 12 \
  --port /dev/ttyACM0
```

Reset current position as the motor midpoint:

```bash
python examples/debug/motors.py reset_motors_to_midpoint \
  --port /dev/ttyACM1
```

Disable torque for all arm motors:

```bash
python examples/debug/motors.py reset_motors_torque \
  --port /dev/ttyACM0
```

Execute an action script on the robot arm:

```bash
python examples/debug/motors.py move_motors_by_script \
  --script_path action_scripts/test_dance.txt \
  --port /dev/ttyACM0
```

These programming commands can change persistent servo settings. Use them only for the specific
hardware-programming procedure that calls for those changes.
