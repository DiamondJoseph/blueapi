"""Devices that declare what they can be set to, and plans that ask for them"""

from typing import Any

from bluesky.protocols import Movable
from bluesky.utils import MsgGenerator
from dodal.device_manager import DeviceManager
from ophyd_async.core import AsyncStatus, Device

devices = DeviceManager()


class Mover(Device):
    @AsyncStatus.wrap
    async def set(self, value: Any) -> None: ...


class AnyMover(Mover, Movable[Any]): ...


class BoolMover(Mover, Movable[bool]): ...


class IntMover(Mover, Movable[int]): ...


class StrMover(Mover, Movable[str]): ...


class IntOrStrMover(Mover, Movable[int | str]): ...


class ObjectMover(Mover, Movable[object]): ...


@devices.factory(mock=True)
def undeclared_mover() -> Mover:
    return Mover()


@devices.factory(mock=True)
def any_mover() -> AnyMover:
    return AnyMover()


@devices.factory(mock=True)
def bool_mover() -> BoolMover:
    return BoolMover()


@devices.factory(mock=True)
def int_mover() -> IntMover:
    return IntMover()


@devices.factory(mock=True)
def str_mover() -> StrMover:
    return StrMover()


@devices.factory(mock=True)
def int_or_str_mover() -> IntOrStrMover:
    return IntOrStrMover()


@devices.factory(mock=True)
def object_mover() -> ObjectMover:
    return ObjectMover()


def needs_mover(mover: Movable) -> MsgGenerator:
    yield from ()


def needs_any_mover(mover: Movable[Any]) -> MsgGenerator:
    yield from ()


def needs_int_mover(mover: Movable[int]) -> MsgGenerator:
    yield from ()


def needs_int_mover_or_str_mover(mover: Movable[int] | Movable[str]) -> MsgGenerator:
    yield from ()


def needs_int_or_str_mover(mover: Movable[int | str]) -> MsgGenerator:
    yield from ()
