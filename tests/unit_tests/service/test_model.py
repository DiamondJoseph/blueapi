from typing import Any

from bluesky.protocols import Movable, Status

from blueapi.service.model import DeviceModel


class Mover:
    name = "mover"

    def set(self, value: Any) -> Status: ...


class IntOrNoneMover(Mover, Movable[int | None]): ...


class LaterMover(Mover, Movable["Later"]): ...


class Later: ...


def movable_types(device: Mover) -> list[str]:
    protocols = DeviceModel.from_device(device).protocols
    return next(protocol.types for protocol in protocols if protocol.name == "Movable")


def test_device_model_names_each_member_of_union_type_argument():
    assert movable_types(IntOrNoneMover()) == ["int | NoneType"]


def test_device_model_describes_type_argument_that_has_no_name():
    # The forward reference is never resolved, so it is not a class with a name
    (described,) = movable_types(LaterMover())
    assert "Later" in described
