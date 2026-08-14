#!/usr/bin/env python

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

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Protocol

logger = logging.getLogger(__name__)

ACK_READBACK_REGISTERS = frozenset({"Torque_Enable", "Lock"})
REGISTER_RETRIES = 2


class RegisterBus(Protocol):
    def read(
        self,
        data_name: str,
        motor: str,
        *,
        normalize: bool = True,
        num_retry: int = 3,
    ) -> int | float: ...

    def write(
        self,
        data_name: str,
        motor: str,
        value: int | float,
        *,
        normalize: bool = True,
        num_retry: int = 3,
    ) -> None: ...


def write_register(
    bus: RegisterBus,
    register: str,
    motor: str,
    value: int,
    *,
    num_retry: int = REGISTER_RETRIES,
) -> None:
    """Write a raw register with a narrow fallback for missing status packets.

    Feetech servos can apply ``Torque_Enable`` or ``Lock`` and still fail to return
    the expected acknowledgement. Only those two registers may use readback as a
    substitute for the acknowledgement; every other write failure remains fatal.
    """
    expected = int(value)
    try:
        bus.write(register, motor, expected, normalize=False, num_retry=num_retry)
        return
    except Exception as write_error:
        if register not in ACK_READBACK_REGISTERS:
            raise

        try:
            actual = int(bus.read(register, motor, normalize=False, num_retry=num_retry))
        except Exception as read_error:
            raise RuntimeError(
                f"Failed to write {register}={expected} for '{motor}', and bounded readback failed."
            ) from read_error

        if actual != expected:
            raise RuntimeError(
                f"Failed to verify {register} for '{motor}': expected {expected}, read back {actual}."
            ) from write_error

        logger.warning(
            "The %s=%s write for '%s' returned no acknowledgement; bounded readback verified it.",
            register,
            expected,
            motor,
        )


def set_torque_enabled(
    bus: RegisterBus,
    motors: Iterable[str],
    *,
    enabled: bool,
    num_retry: int = REGISTER_RETRIES,
) -> None:
    """Set torque and the matching EEPROM lock state for each selected motor."""
    value = int(enabled)
    for motor in motors:
        write_register(bus, "Torque_Enable", motor, value, num_retry=num_retry)
        write_register(bus, "Lock", motor, value, num_retry=num_retry)
