from __future__ import annotations

import logging
from dataclasses import dataclass, field
from inspect import Parameter
from pathlib import Path
from types import GenericAlias, ModuleType, NoneType, new_class
from typing import Any, Generic, TypeVar, Union, get_args, get_type_hints
from unittest.mock import MagicMock, Mock, patch

import pytest
import responses
from bluesky.protocols import (
    Descriptor,
    Movable,
    Readable,
    Reading,
    Status,
    Stoppable,
    SyncOrAsync,
)
from bluesky.run_engine import RunEngine
from bluesky.utils import MsgGenerator
from dodal.common import PlanGenerator, inject
from ophyd_async.core import (
    Device,
    DeviceVector,
    PathProvider,
    StandardDetector,
    StaticPathProvider,
    UUIDFilenameProvider,
    init_devices,
    soft_signal_rw,
)
from ophyd_async.epics.adaravis import AravisDetector
from ophyd_async.epics.adcore import ADWriterFactory
from ophyd_async.epics.motor import Motor
from ophyd_async.sim import SimMotor
from pydantic import TypeAdapter, ValidationError
from pydantic.json_schema import SkipJsonSchema
from pytest import LogCaptureFixture
from tests.unit_tests.core.fake_generic_module import (
    needs_any_mover,
    needs_int_mover,
    needs_int_mover_or_str_mover,
    needs_int_or_str_mover,
    needs_mover,
)

from blueapi.config import (
    ApplicationConfig,
    DeviceManagerSource,
    EnvironmentConfig,
    MetadataConfig,
    OIDCConfig,
    PlanSource,
    ServiceAccount,
    TiledConfig,
)
from blueapi.core import BlueskyContext, is_bluesky_compatible_device
from blueapi.core.bluesky_types import BlueapiDevice
from blueapi.core.context import (
    generic_bounds,
    is_compatible_args,
    qualified_name,
)
from blueapi.core.protocols import DeviceConnectResult, DeviceManager
from blueapi.utils.invalid_config_error import InvalidConfigError

SIM_MOTOR_NAME = "sim"
ALT_MOTOR_NAME = "alt"
EXPECTED_PLANS = {
    "plan_a",
    "plan_b",
    "plan_c",
    "plan_d",
    "plan_e",
    "plan_f",
    "plan_g",
    "plan_h",
    "plan_i",
    "plan_j",
    "plan_k",
    "plan_l",
    "plan_m",
    "plan_n",
}
EXPECTED_FAKE_DEVICE_MODULE_DEVICES = {
    "fake_motor_x",
    "fake_motor_y",
    "fake_motor_bundle_a",
    "fake_motor_bundle_b",
    "device_a",
    "device_a-motor",
}


def assert_fake_device_module_registered(context: BlueskyContext) -> None:
    # Which child signals exist depends on the upstream device classes
    assert EXPECTED_FAKE_DEVICE_MODULE_DEVICES <= context.devices.keys()
    assert all(
        name.split("-")[0] in EXPECTED_FAKE_DEVICE_MODULE_DEVICES
        for name in context.devices
    )


#
# Dummy plans
#


def has_no_params() -> MsgGenerator:
    yield from ()


def has_one_param(foo: int) -> MsgGenerator:
    yield from ()


def has_some_params(foo: int = 42, bar: str = "bar") -> MsgGenerator:
    yield from ()


def has_optional_parameter(foo: dict[str, Any] | None = None) -> MsgGenerator:
    yield from ()


def has_typeless_param(foo) -> MsgGenerator:
    yield from ()


def has_typed_and_typeless_params(foo: int, bar) -> MsgGenerator:
    yield from ()


def has_typeless_params(foo, bar) -> MsgGenerator:
    yield from ()


MOTOR: Movable = inject(SIM_MOTOR_NAME)


def has_default_reference(m: Movable = MOTOR) -> MsgGenerator:
    yield from []


MOVABLE_DEFAULT = [inject(SIM_MOTOR_NAME)]


def has_default_nested_reference(
    m: list[Movable] = MOVABLE_DEFAULT,
) -> MsgGenerator:
    yield from []


#
# Dummy devices
#


@pytest.fixture
def sim_motor(run_engine: RunEngine) -> Motor:
    with init_devices(mock=True):
        sim = Motor("FOO:")
    return sim


@pytest.fixture
def alt_motor(run_engine: RunEngine) -> Motor:
    with init_devices(mock=True):
        alt = Motor("BAR:")
    return alt


@pytest.fixture
def sim_detector(tmp_path: Path) -> StandardDetector:
    path_provider = StaticPathProvider(UUIDFilenameProvider(), tmp_path)
    with init_devices(mock=True):
        sim_det = AravisDetector("DET:", ADWriterFactory.hdf(path_provider))
    return sim_det


@pytest.fixture
def empty_context() -> BlueskyContext:
    return BlueskyContext()


@pytest.fixture
def devicey_context(sim_motor: Motor, sim_detector: StandardDetector) -> BlueskyContext:
    ctx = BlueskyContext()
    ctx.register_device(sim_motor)
    ctx.register_device(sim_detector)
    return ctx


class SomeConfigurable:
    def read_configuration(self) -> SyncOrAsync[dict[str, Reading]]:
        return {}

    def describe_configuration(self) -> SyncOrAsync[dict[str, Descriptor]]:
        return {}


@pytest.fixture
def some_configurable() -> SomeConfigurable:
    return SomeConfigurable()


@pytest.mark.parametrize(
    "plan", [has_no_params, has_one_param, has_some_params, has_optional_parameter]
)
def test_add_plan(empty_context: BlueskyContext, plan: PlanGenerator):
    empty_context.register_plan(plan)
    assert plan.__name__ in empty_context.plans


def test_generated_schema(
    devicey_context: BlueskyContext,
):
    def demo_plan(foo: int, mov: Movable) -> MsgGenerator:
        yield from ()

    devicey_context.register_plan(demo_plan)
    schema = devicey_context.plans["demo_plan"].model.model_json_schema()

    # Which child signals exist depends on the upstream Motor class
    expected_movables = sorted(
        name
        for name, entry in devicey_context.devices.items()
        if isinstance(entry.device, Movable)
    )
    assert {"sim", "sim-velocity"} <= set(expected_movables)
    assert schema["properties"] == {
        "foo": {"title": "Foo", "type": "integer"},
        "mov": {
            "title": "Mov",
            "type": "bluesky.protocols.Movable",
            "enum": expected_movables,
        },
    }


def test_generated_schema_with_generic_bounds(
    empty_context: BlueskyContext,
):
    def demo_plan(foo: int, mov: Movable[int]) -> MsgGenerator:
        yield from ()

    empty_context.register_plan(demo_plan)
    schema = empty_context.plans["demo_plan"].model.model_json_schema()
    assert schema["properties"] == {
        "foo": {"title": "Foo", "type": "integer"},
        "mov": {
            "title": "Mov",
            "type": "bluesky.protocols.Movable",
            "types": ["int"],
            "enum": [],
        },
    }


@pytest.mark.parametrize(
    "plan", [has_typeless_param, has_typed_and_typeless_params, has_typeless_params]
)
def test_add_invalid_plan(empty_context: BlueskyContext, plan: PlanGenerator):
    with pytest.raises(ValueError):
        empty_context.register_plan(plan)


def test_add_plan_from_module(empty_context: BlueskyContext):
    import tests.unit_tests.core.fake_plan_module as plan_module

    empty_context.with_plan_module(plan_module)
    assert EXPECTED_PLANS == empty_context.plans.keys()


def test_only_plans_from_source_module_detected(empty_context: BlueskyContext):
    import tests.unit_tests.core.fake_plan_module_with_imports as plan_module

    empty_context.with_plan_module(plan_module)
    assert {"plan_c", "plan_d"} == empty_context.plans.keys()


def test_only_plans_from_all_in_module_detected(empty_context: BlueskyContext):
    import tests.unit_tests.core.fake_plan_module_with_all as plan_module

    empty_context.with_plan_module(plan_module)
    assert {"plan_a", "plan_d"} == empty_context.plans.keys()


def test_add_named_device(empty_context: BlueskyContext, sim_motor: Motor):
    empty_context.register_device(sim_motor)
    assert empty_context.devices[SIM_MOTOR_NAME].device is sim_motor


def test_add_nameless_device(
    empty_context: BlueskyContext, some_configurable: SomeConfigurable
):
    empty_context.register_device(some_configurable, "conf")
    assert empty_context.devices["conf"].device is some_configurable


def test_add_nameless_device_without_override(
    empty_context: BlueskyContext,
    some_configurable: SomeConfigurable,
):
    with pytest.raises(KeyError):
        empty_context.register_device(some_configurable)


def test_override_device_name(empty_context: BlueskyContext, sim_motor: Motor):
    empty_context.register_device(sim_motor, "foo")
    assert empty_context.devices["foo"].device is sim_motor


class Axis(Device):
    def __init__(self, name: str = "") -> None:
        self.position = soft_signal_rw(float)
        self.velocity = soft_signal_rw(float)
        super().__init__(name=name)


class Stage(Device):
    def __init__(self, name: str = "") -> None:
        self.x = Axis()
        self.y = Axis()
        super().__init__(name=name)


class Table(Device):
    def __init__(self, name: str = "") -> None:
        self.mount = Stage()
        super().__init__(name=name)


def test_register_device_registers_children_recursively(
    empty_context: BlueskyContext,
):
    table = Table(name="table")
    empty_context.register_device(table)

    assert empty_context.devices.keys() == {
        "table",
        "table-mount",
        "table-mount-x",
        "table-mount-x-position",
        "table-mount-x-velocity",
        "table-mount-y",
        "table-mount-y-position",
        "table-mount-y-velocity",
    }
    assert empty_context.devices["table-mount"].device is table.mount
    assert empty_context.devices["table-mount-x"].device is table.mount.x
    assert (
        empty_context.devices["table-mount-x-velocity"].device is table.mount.x.velocity
    )


def test_child_sharing_parent_name_is_skipped_and_warned_once(
    empty_context: BlueskyContext, caplog: LogCaptureFixture
):
    # SimMotor's readback signal has the same name as the motor
    caplog.set_level(logging.DEBUG)
    motor = SimMotor(name="motor")
    empty_context.register_device(motor)
    empty_context.register_device(SimMotor(name="other"))

    assert empty_context.devices["motor"].device is motor
    assert motor.user_readback not in empty_context.devices.values()
    assert empty_context.find_device("motor.user_readback") is motor.user_readback
    records = caplog.get_records("call")
    assert [record.levelno for record in records] == [
        logging.WARNING,
        logging.DEBUG,
        logging.DEBUG,
    ]
    assert "'motor'" in records[1].message
    assert "'other'" in records[2].message


class AxisBank(Device):
    def __init__(self, name: str = "") -> None:
        self.axes = DeviceVector({1: Axis(), 2: Axis()})
        super().__init__(name=name)


def test_device_vector_children_are_registered(empty_context: BlueskyContext):
    bank = AxisBank(name="bank")
    empty_context.register_device(bank)

    assert empty_context.devices["bank-axes"].device is bank.axes
    assert empty_context.devices["bank-axes-1"].device is bank.axes[1]
    assert empty_context.devices["bank-axes-2-velocity"].device is bank.axes[2].velocity


def test_overridden_registration_name_does_not_rename_children(
    empty_context: BlueskyContext,
):
    # Children keep their own names when the parent is registered as "foo", so
    # the motor's readback is registered as "motor", which is not in use
    motor = SimMotor(name="motor")
    empty_context.register_device(motor, name="foo")

    assert empty_context.devices["foo"].device is motor
    assert empty_context.devices["motor"].device is motor.user_readback
    assert empty_context.devices["motor-velocity"].device is motor.velocity


def test_unrelated_name_collision_keeps_existing_device_and_logs(
    empty_context: BlueskyContext, caplog: LogCaptureFixture
):
    sentinel = SimMotor(name="sentinel")
    empty_context.register_device(sentinel, name="stage-x")

    caplog.set_level(logging.DEBUG)
    stage = Stage(name="stage")
    empty_context.register_device(stage)

    assert empty_context.devices["stage-x"].device is sentinel
    assert "stage-x-position" not in empty_context.devices
    assert empty_context.devices["stage-y"].device is stage.y
    assert any("stage-x" in record.message for record in caplog.get_records("call"))


def test_top_level_name_collision_keeps_existing_device_and_logs(
    empty_context: BlueskyContext, caplog: LogCaptureFixture
):
    stage = Stage(name="stage")
    empty_context.register_device(stage)

    caplog.set_level(logging.DEBUG)
    motor = SimMotor(name="stage-x")
    empty_context.register_device(motor)

    assert empty_context.devices["stage-x"].device is stage.x
    assert motor.velocity not in empty_context.devices.values()
    assert any("stage-x" in record.message for record in caplog.get_records("call"))


def test_register_device_is_idempotent(
    empty_context: BlueskyContext, caplog: LogCaptureFixture
):
    stage = Stage(name="stage")
    empty_context.register_device(stage)
    before = dict(empty_context.devices)

    caplog.set_level(logging.WARNING)
    empty_context.register_device(stage)

    assert empty_context.devices == before
    assert not caplog.get_records("call")


def test_register_device_skips_unnamed_children(empty_context: BlueskyContext):
    stage = Stage()  # unnamed, so its children are too
    empty_context.register_device(stage, name="stage")

    assert empty_context.devices.keys() == {"stage"}


def test_child_device_usable_as_plan_argument(empty_context: BlueskyContext):
    stage = Stage(name="stage")
    empty_context.register_device(stage)

    def move_plan(mov: Movable) -> MsgGenerator:
        yield from ()

    empty_context.register_plan(move_plan)
    model = empty_context.plans[move_plan.__name__].model

    position = stage.x.position
    assert model.model_validate({"mov": "stage-x-position"}).mov is position  # type: ignore
    assert model.model_validate({"mov": "stage.x.position"}).mov is position  # type: ignore


def test_add_devices_from_module(empty_context: BlueskyContext):
    import tests.unit_tests.core.fake_device_module as device_module

    empty_context.with_device_manager(device_module.devices)  # type: ignore - protocol uses Any to avoid dependency on dodal
    assert_fake_device_module_registered(empty_context)


def test_add_failing_devices_from_module(
    caplog: LogCaptureFixture, empty_context: BlueskyContext
):
    import tests.unit_tests.core.fake_device_module_failing as device_module

    caplog.set_level(10)
    empty_context.with_device_manager(device_module.devices)  # type: ignore - protocol uses Any to avoid dependency on dodal
    logs = caplog.get_records("call")

    assert any("TimeoutError('FooBar')" in log.message for log in logs)
    assert len(empty_context.devices.keys()) == 0


@pytest.mark.parametrize(
    "addr", ["sim", "sim_det", "sim.user_setpoint", ["sim"], ["sim", "user_setpoint"]]
)
def test_lookup_device(devicey_context: BlueskyContext, addr: str | list[str]):
    device = devicey_context.find_device(addr)
    assert is_bluesky_compatible_device(device)


def test_lookup_nonexistent_device(devicey_context: BlueskyContext):
    assert devicey_context.find_device("foo") is None


def test_lookup_nonexistent_device_child(devicey_context: BlueskyContext):
    assert devicey_context.find_device("sim.foo") is None


def test_lookup_non_device(devicey_context: BlueskyContext):
    with pytest.raises(ValueError):
        devicey_context.find_device("sim.name")


def test_add_non_plan(empty_context: BlueskyContext):
    with pytest.raises(TypeError):
        empty_context.register_plan("not a plan")  # type: ignore


def test_add_non_device(empty_context: BlueskyContext):
    with pytest.raises(TypeError):
        empty_context.register_device("not a device")  # type: ignore


def test_add_devices_and_plans_from_modules_with_config(
    empty_context: BlueskyContext,
):
    empty_context.with_config(
        EnvironmentConfig(
            sources=[
                DeviceManagerSource(
                    module="tests.unit_tests.core.fake_device_module",
                ),
                PlanSource(
                    module="tests.unit_tests.core.fake_plan_module",
                ),
            ]
        )
    )
    assert_fake_device_module_registered(empty_context)
    assert EXPECTED_PLANS == empty_context.plans.keys()


def test_add_metadata_with_config(
    empty_context: BlueskyContext,
):
    empty_context.with_config(
        EnvironmentConfig(metadata=MetadataConfig(instrument="p46"))
    )
    metadata = [("instrument", "p46")]

    for md in metadata:
        assert md in empty_context.run_engine.md.items()


def test_function_spec_with_some_params(empty_context: BlueskyContext):
    spec = empty_context._type_spec_for_function(has_some_params)
    assert spec["foo"][0] is int
    assert spec["foo"][1].default == 42
    assert spec["bar"][0] is str
    assert spec["bar"][1].default == "bar"


def test_function_spec_with_optional_params(empty_context: BlueskyContext):
    spec = empty_context._type_spec_for_function(has_optional_parameter)
    types = get_type_hints(has_optional_parameter)
    arg_type = types.get("foo", Parameter.empty)

    _type = SkipJsonSchema[empty_context._convert_type(arg_type, False)]
    inner_type, *annotations = get_args(_type)
    assert spec["foo"][0] == inner_type
    assert spec["foo"][1].default_factory() is None  # type: ignore


def test_basic_type_conversion(empty_context: BlueskyContext):
    assert empty_context._convert_type(int) is int
    assert empty_context._convert_type(dict[str, int]) == dict[str, int]


def test_device_reference_cache(empty_context: BlueskyContext):
    assert empty_context._reference(Movable) is empty_context._reference(Movable)
    assert empty_context._reference(Movable) is not empty_context._reference(Readable)


def test_device_reference_cache_with_generics(empty_context: BlueskyContext):
    motor = Movable[float]
    assert empty_context._reference(motor) is empty_context._reference(motor)
    assert empty_context._reference(motor) is not empty_context._reference(Movable[int])
    assert empty_context._reference(motor) is not empty_context._reference(Movable)


def test_unregister_all_devices(devicey_context: BlueskyContext):
    assert len(devicey_context.devices) > 0
    devicey_context.unregister_all_devices()

    assert len(devicey_context.devices) == 0


def test_reference_type_conversion(empty_context: BlueskyContext):
    movable_ref: type = empty_context._reference(Movable)
    assert empty_context._convert_type(Movable) == movable_ref
    assert (
        empty_context._convert_type(dict[Movable, list[tuple[int, Movable]]])
        == dict[movable_ref, list[tuple[int, movable_ref]]]
    )


def test_generic_reference_type_conversion(empty_context: BlueskyContext):
    motor = Movable[float]
    motor_ref: type = empty_context._reference(motor)
    assert empty_context._convert_type(motor) == motor_ref
    assert (
        empty_context._convert_type(dict[motor, list[tuple[int, motor]]])
        == dict[motor_ref, list[tuple[int, motor_ref]]]
    )


def test_reference_type_conversion_union(empty_context: BlueskyContext):
    movable_ref: type = empty_context._reference(Movable)
    assert empty_context._convert_type(Movable) == movable_ref
    assert (
        empty_context._convert_type(Union[Movable, int]) == Union[movable_ref, int]  # noqa # type: ignore
    )


def test_reference_type_conversion_new_style_union(
    empty_context: BlueskyContext,
):
    movable_ref: type = empty_context._reference(Movable)
    assert empty_context._convert_type(Movable) == movable_ref
    assert empty_context._convert_type(Movable | int) == movable_ref | int


def test_reference_type_conversion_new_style_optional(
    empty_context: BlueskyContext,
):
    movable_ref: type = empty_context._reference(Movable)
    assert empty_context._convert_type(Movable) == movable_ref
    assert empty_context._convert_type(Movable | None) == movable_ref | None
    assert (
        empty_context._convert_type(Movable | None, no_default=False)
        == movable_ref | SkipJsonSchema[NoneType]
    )


def test_default_device_reference(empty_context: BlueskyContext):
    def default_movable(mov: Movable = inject("demo")) -> MsgGenerator:
        yield from ()

    spec = empty_context._type_spec_for_function(default_movable)
    movable_ref = empty_context._reference(Movable)
    assert spec["mov"][0] == movable_ref
    assert spec["mov"][1].default == "demo"


def test_generic_default_device_reference(empty_context: BlueskyContext):
    def default_movable(mov: Movable[float] = inject("demo")) -> MsgGenerator:
        yield from ()

    spec = empty_context._type_spec_for_function(default_movable)
    motor_ref = empty_context._reference(Movable[float])
    assert spec["mov"][0] == motor_ref
    assert spec["mov"][1].default == "demo"


class ConcreteStoppable(Stoppable):
    """Concrete implementation of a Bluesky protocol"""

    @property
    def name(self) -> str:
        return "Concrete"

    def stop(self, success: bool = True):
        pass


def test_concrete_type_conversion(empty_context: BlueskyContext):
    stoppable_ref = empty_context._reference(ConcreteStoppable)
    assert empty_context._convert_type(ConcreteStoppable) == stoppable_ref


def test_concrete_method_annotation(empty_context: BlueskyContext):
    stoppable_ref = empty_context._reference(ConcreteStoppable)

    def demo(named: ConcreteStoppable): ...

    spec = empty_context._type_spec_for_function(demo)
    assert spec["named"][0] is stoppable_ref
    assert spec["named"][1].default_factory is None


class PlainDevice(Device):
    """Class that extends Device without any additional protocols"""


def test_device_without_protocol_annotation(empty_context: BlueskyContext):
    dev_ref = empty_context._reference(PlainDevice)

    def demo_plan(dev: PlainDevice) -> MsgGenerator:
        yield from []

    spec = empty_context._type_spec_for_function(demo_plan)
    assert spec["dev"][0] is dev_ref


def test_str_default(empty_context: BlueskyContext, sim_motor: Motor, alt_motor: Motor):
    movable_ref = empty_context._reference(Movable)
    empty_context.register_device(sim_motor)
    empty_context.register_plan(has_default_reference)

    spec = empty_context._type_spec_for_function(has_default_reference)
    assert spec["m"][0] is movable_ref
    assert spec["m"][1].default == SIM_MOTOR_NAME

    assert has_default_reference.__name__ in empty_context.plans
    model = empty_context.plans[has_default_reference.__name__].model
    adapter = TypeAdapter(model)
    assert adapter.validate_python({}).m is sim_motor  # type: ignore
    empty_context.register_device(alt_motor)
    assert adapter.validate_python({"m": ALT_MOTOR_NAME}).m is alt_motor  # type: ignore


def test_nested_str_default(
    empty_context: BlueskyContext, sim_motor: Motor, alt_motor: Motor
):
    movable_ref = empty_context._reference(Movable)
    empty_context.register_device(sim_motor)
    empty_context.register_plan(has_default_nested_reference)

    spec = empty_context._type_spec_for_function(has_default_nested_reference)
    assert spec["m"][0] == list[movable_ref]
    assert spec["m"][1].default == [SIM_MOTOR_NAME]

    assert has_default_nested_reference.__name__ in empty_context.plans
    model = empty_context.plans[has_default_nested_reference.__name__].model
    adapter = TypeAdapter(model)

    assert adapter.validate_python({}).m == [sim_motor]  # type: ignore
    empty_context.register_device(alt_motor)
    assert adapter.validate_python({"m": [ALT_MOTOR_NAME]}).m == [alt_motor]  # type: ignore


def test_plan_models_not_auto_camelcased(empty_context: BlueskyContext):
    def a_plan(foo_bar: int, baz: str) -> MsgGenerator:
        if False:
            yield

    empty_context.register_plan(a_plan)
    with pytest.raises(ValidationError):
        empty_context.plans[a_plan.__name__].model(fooBar=1, baz="test")


def test_generic_bounds_with_generic_base():
    T = TypeVar("T")

    class Base(Generic[T]):
        pass

    class Derived(Base[int]):
        pass

    derived_instance = Derived()
    assert generic_bounds(derived_instance, Base) == (int,)  # type: ignore


def test_generic_bounds_with_multiple_bases():
    T = TypeVar("T")

    class Base1(Generic[T]):
        pass

    class Base2:
        pass

    class Derived(Base1[int], Base2):
        pass

    derived_instance = Derived()
    assert generic_bounds(derived_instance, Base1) == (int,)  # type: ignore
    assert generic_bounds(derived_instance, Base2) == ()  #  type: ignore


def test_generic_bounds_with_no_bases():
    class Base:
        pass

    class Derived:
        pass

    derived_instance = Derived()
    assert generic_bounds(derived_instance, Base) == ()  # type: ignore


T_inv = TypeVar("T_inv")
T_co = TypeVar("T_co", covariant=True)
T_contra = TypeVar("T_contra", contravariant=True)


class Both(Generic[T_inv]): ...


class Producer(Generic[T_co]): ...


class Consumer(Generic[T_contra]): ...


def device_declaring(base: Any, type_argument: Any) -> Any:
    """An instance of a class that directly declares base[type_argument]"""
    return new_class("Declared", (base[type_argument],))()


# Each case is the type argument declared by a device and whether that device
# satisfies an annotation whose type argument is int
type_argument_ids = ["subtype", "same", "supertype", "unrelated"]


@pytest.mark.parametrize(
    "declared,compatible",
    [(bool, True), (int, True), (object, False), (str, False)],
    ids=type_argument_ids,
)
def test_covariant_type_argument_accepts_subtypes(declared: type, compatible: bool):
    device = device_declaring(Producer, declared)
    assert is_compatible_args(device, Producer, (int,)) is compatible


@pytest.mark.parametrize(
    "declared,compatible",
    [(bool, False), (int, True), (object, True), (str, False)],
    ids=type_argument_ids,
)
def test_contravariant_type_argument_accepts_supertypes(
    declared: type, compatible: bool
):
    device = device_declaring(Consumer, declared)
    assert is_compatible_args(device, Consumer, (int,)) is compatible


@pytest.mark.parametrize(
    "declared,compatible",
    [(bool, True), (int, True), (object, False), (str, False)],
    ids=type_argument_ids,
)
def test_invariant_type_argument_accepts_subtypes(declared: type, compatible: bool):
    # Lenient on purpose: protocols that only produce values, such as bluesky's
    # Readable, are often declared invariant
    device = device_declaring(Both, declared)
    assert is_compatible_args(device, Both, (int,)) is compatible


class Converter(Generic[T_contra, T_co]): ...


@pytest.mark.parametrize(
    "consumes,produces,compatible",
    [
        (int, int, True),
        (object, bool, True),
        (bool, bool, False),
        (object, object, False),
    ],
)
def test_each_type_argument_is_compared_with_its_own_variance(
    consumes: type, produces: type, compatible: bool
):
    device = device_declaring(Converter, (consumes, produces))
    type_arguments = get_args(Converter[int, int])
    assert is_compatible_args(device, Converter, type_arguments) is compatible


class Subscriptable:
    __class_getitem__ = classmethod(GenericAlias)


@pytest.mark.parametrize(
    "declared,compatible",
    [(bool, True), (int, True), (object, False), (str, False)],
    ids=type_argument_ids,
)
def test_type_argument_without_type_parameter_accepts_subtypes(
    declared: type, compatible: bool
):
    # A class can take type arguments without declaring any type parameters
    device = device_declaring(Subscriptable, declared)
    assert is_compatible_args(device, Subscriptable, (int,)) is compatible


@pytest.mark.parametrize("base", [Both, Producer, Consumer])
def test_device_declaring_any_is_compatible(base: type):
    assert is_compatible_args(device_declaring(base, Any), base, (int,))


@pytest.mark.parametrize("base", [Both, Producer, Consumer])
def test_device_without_type_arguments_is_compatible(base: type):
    assert is_compatible_args(new_class("Undeclared", (base,))(), base, (int,))


@pytest.mark.parametrize("base", [Both, Producer, Consumer])
def test_annotation_without_type_arguments_accepts_any_device(base: type):
    assert is_compatible_args(device_declaring(base, str), base, None)


@pytest.mark.parametrize("base", [Both, Producer])
@pytest.mark.parametrize("declared", [str, int | str, list[int]])
def test_produced_type_annotated_with_any_accepts_any_device(base: Any, declared: Any):
    type_arguments = get_args(base[Any])
    assert is_compatible_args(device_declaring(base, declared), base, type_arguments)


# An annotation of Any for a type that a device consumes means that the device
# could be given anything
@pytest.mark.parametrize(
    "declared,compatible",
    [(Any, True), (object, True), (str, False), (int | str, False)],
)
def test_consumed_type_annotated_with_any_needs_device_that_takes_anything(
    declared: Any, compatible: bool
):
    device = device_declaring(Consumer, declared)
    type_arguments = get_args(Consumer[Any])
    assert is_compatible_args(device, Consumer, type_arguments) is compatible


@pytest.mark.parametrize(
    "declared,expected,compatible",
    [
        (int, int | str, True),
        (int | str, int | str, True),
        (int | bytes, int | str, False),
        (int | str, int, False),
    ],
)
def test_covariant_union_type_argument_must_be_covered_by_annotation(
    declared: Any, expected: Any, compatible: bool
):
    device = device_declaring(Producer, declared)
    assert is_compatible_args(device, Producer, (expected,)) is compatible


@pytest.mark.parametrize(
    "declared,compatible", [(list[int], True), (list[bool], False)]
)
def test_type_arguments_that_cannot_be_compared_must_be_equal(
    declared: Any, compatible: bool
):
    device = device_declaring(Producer, declared)
    assert is_compatible_args(device, Producer, (list[int],)) is compatible


class Mover:
    def __init__(self, name: str) -> None:
        self.name = name

    def set(self, value: Any) -> Status: ...


class AnyMover(Mover, Movable[Any]): ...


class BoolMover(Mover, Movable[bool]): ...


class IntMover(Mover, Movable[int]): ...


class StrMover(Mover, Movable[str]): ...


class IntOrStrMover(Mover, Movable[int | str]): ...


class ObjectMover(Mover, Movable[object]): ...


def device_passed_to(context: BlueskyContext, plan: PlanGenerator, mover: Mover) -> Any:
    """The device that the plan would be run with when asked for the mover"""
    context.register_device(mover)
    context.register_plan(plan)
    model = context.plans[plan.__name__].model
    return dict(model.model_validate({"mover": mover.name}))["mover"]


# bluesky declares Movable as contravariant in the type of value it is set to
@pytest.mark.parametrize("mover_type", [AnyMover, IntMover, IntOrStrMover, ObjectMover])
def test_movable_parameter_accepts_device_that_takes_wider_values(
    empty_context: BlueskyContext, mover_type: type[Mover]
):
    mover = mover_type("mover")
    assert device_passed_to(empty_context, needs_int_mover, mover) is mover


def test_movable_parameter_rejects_device_that_takes_narrower_values(
    empty_context: BlueskyContext,
):
    with pytest.raises(
        ValidationError, match=r"is not of type bluesky.protocols.Movable\[int\]"
    ):
        device_passed_to(empty_context, needs_int_mover, BoolMover("mover"))


@pytest.mark.parametrize(
    "mover_type",
    [Mover, AnyMover, BoolMover, IntMover, StrMover, IntOrStrMover, ObjectMover],
)
def test_movable_parameter_without_type_argument_accepts_any_mover(
    empty_context: BlueskyContext, mover_type: type[Mover]
):
    mover = mover_type("mover")
    assert device_passed_to(empty_context, needs_mover, mover) is mover


# A plan that asks for Movable[Any] could set its device to anything
@pytest.mark.parametrize("mover_type", [Mover, AnyMover, ObjectMover])
def test_movable_of_any_accepts_device_that_takes_anything(
    empty_context: BlueskyContext, mover_type: type[Mover]
):
    mover = mover_type("mover")
    assert device_passed_to(empty_context, needs_any_mover, mover) is mover


@pytest.mark.parametrize(
    "mover_type,declared",
    [
        (BoolMover, "bool"),
        (IntMover, "int"),
        (StrMover, "str"),
        (IntOrStrMover, r"int \| str"),
    ],
)
def test_movable_of_any_rejects_device_that_takes_only_some_values(
    empty_context: BlueskyContext, mover_type: type[Mover], declared: str
):
    with pytest.raises(
        ValidationError,
        match=(
            r"is not of type bluesky\.protocols\.Movable\[typing\.Any\], "
            rf"it is a bluesky\.protocols\.Movable\[{declared}\]"
        ),
    ):
        device_passed_to(empty_context, needs_any_mover, mover_type("mover"))


@pytest.mark.parametrize(
    "mover_type", [AnyMover, IntMover, StrMover, IntOrStrMover, ObjectMover]
)
def test_union_of_movables_accepts_device_that_satisfies_any_member(
    empty_context: BlueskyContext, mover_type: type[Mover]
):
    mover = mover_type("mover")
    passed = device_passed_to(empty_context, needs_int_mover_or_str_mover, mover)
    assert passed is mover


def test_union_of_movables_rejects_device_that_satisfies_no_member(
    empty_context: BlueskyContext,
):
    with pytest.raises(ValidationError, match="is not of type"):
        device_passed_to(
            empty_context, needs_int_mover_or_str_mover, BoolMover("mover")
        )


@pytest.mark.parametrize("mover_type", [AnyMover, IntOrStrMover, ObjectMover])
def test_movable_of_union_accepts_device_that_takes_every_member(
    empty_context: BlueskyContext, mover_type: type[Mover]
):
    mover = mover_type("mover")
    assert device_passed_to(empty_context, needs_int_or_str_mover, mover) is mover


@pytest.mark.parametrize(
    "mover_type,declared", [(BoolMover, "bool"), (IntMover, "int"), (StrMover, "str")]
)
def test_movable_of_union_rejects_device_that_takes_only_some_members(
    empty_context: BlueskyContext, mover_type: type[Mover], declared: str
):
    with pytest.raises(
        ValidationError,
        match=(
            rf"Device mover \(.*{mover_type.__name__}\) is not of type "
            r"bluesky\.protocols\.Movable\[int \| str\], "
            rf"it is a bluesky\.protocols\.Movable\[{declared}\]"
        ),
    ):
        device_passed_to(empty_context, needs_int_or_str_mover, mover_type("mover"))


def test_generated_schema_with_union_type_argument(empty_context: BlueskyContext):
    empty_context.register_plan(needs_int_or_str_mover)
    schema = empty_context.plans["needs_int_or_str_mover"].model.model_json_schema()
    assert schema["properties"]["mover"]["types"] == ["int | str"]


class LaterMover(Mover, Movable["Later"]): ...


class Later: ...


def test_rejected_device_with_unnamed_type_argument_is_described(
    empty_context: BlueskyContext,
):
    with pytest.raises(ValidationError, match=r"it is a .*Movable\[.*Later.*\]"):
        device_passed_to(empty_context, needs_int_mover, LaterMover("mover"))


GENERIC_MODULE = "tests.unit_tests.core.fake_generic_module"
ALL_MOVERS = {
    "undeclared_mover",
    "any_mover",
    "bool_mover",
    "int_mover",
    "str_mover",
    "int_or_str_mover",
    "object_mover",
}
# The devices in the module that each of its plans accepts as the mover
ACCEPTED_MOVERS = {
    "needs_mover": ALL_MOVERS,
    "needs_any_mover": {"undeclared_mover", "any_mover", "object_mover"},
    "needs_int_mover": ALL_MOVERS - {"bool_mover", "str_mover"},
    "needs_int_mover_or_str_mover": ALL_MOVERS - {"bool_mover"},
    "needs_int_or_str_mover": ALL_MOVERS - {"bool_mover", "int_mover", "str_mover"},
}


@pytest.fixture
def generic_context() -> BlueskyContext:
    """Devices and plans loaded from a module in the same way as the server"""
    context = BlueskyContext()
    context.with_config(
        EnvironmentConfig(
            sources=[
                DeviceManagerSource(module=GENERIC_MODULE),
                PlanSource(module=GENERIC_MODULE),
            ]
        )
    )
    return context


def test_generic_devices_and_plans_are_loaded(generic_context: BlueskyContext):
    assert generic_context.devices.keys() == ALL_MOVERS
    assert generic_context.plans.keys() == ACCEPTED_MOVERS.keys()


@pytest.mark.parametrize(
    "plan,mover",
    [
        (plan, mover)
        for plan in ACCEPTED_MOVERS
        for mover in sorted(ACCEPTED_MOVERS[plan])
    ],
)
def test_plan_accepts_loaded_device(
    generic_context: BlueskyContext, plan: str, mover: str
):
    validated = generic_context.plans[plan].model.model_validate({"mover": mover})
    assert dict(validated)["mover"] is generic_context.devices[mover].device


@pytest.mark.parametrize(
    "plan,mover",
    [
        (plan, mover)
        for plan in ACCEPTED_MOVERS
        for mover in sorted(ALL_MOVERS - ACCEPTED_MOVERS[plan])
    ],
)
def test_plan_rejects_loaded_device(
    generic_context: BlueskyContext, plan: str, mover: str
):
    with pytest.raises(ValidationError, match="is not of type"):
        generic_context.plans[plan].model.model_validate({"mover": mover})


@pytest.mark.parametrize("plan", ACCEPTED_MOVERS)
def test_schema_offers_the_loaded_devices_that_plan_accepts(
    generic_context: BlueskyContext, plan: str
):
    schema = generic_context.plans[plan].model.model_json_schema()
    mover = schema["properties"]["mover"]
    # A union of device types offers the devices of each of its members
    offered = [
        name for member in mover.get("anyOf", [mover]) for name in member["enum"]
    ]
    assert set(offered) == ACCEPTED_MOVERS[plan]


T = TypeVar("T")


class CustomClass: ...


class GenericClass(Generic[T]): ...


@dataclass
class OuterClass:
    class InnerClass: ...


qualified_name_test_data = [
    (int, "int"),
    (float, "float"),
    (str, "str"),
    (list[GenericClass], "list"),
    (list[int], "list"),
    (dict[str, int], "dict"),
    (CustomClass, "core.test_context.CustomClass"),
    (T, "Any"),
    (GenericClass, "core.test_context.GenericClass"),
    (OuterClass.InnerClass, "core.test_context.OuterClass.InnerClass"),
    (int | str, "int | str"),
    (int | CustomClass | None, "int | core.test_context.CustomClass | NoneType"),
    (
        Movable[int] | Movable[str],
        "bluesky.protocols.Movable | bluesky.protocols.Movable",
    ),
]


@pytest.mark.parametrize("type,expected", qualified_name_test_data)
def test_qualified_name_with_types(type: type, expected: str):
    assert qualified_name(type) == expected


def test_optional_arg_generated_schema(
    empty_context: BlueskyContext,
):
    def demo_plan(foo: int | None = None) -> MsgGenerator:
        yield from ()

    empty_context.register_plan(demo_plan)
    schema = empty_context.plans["demo_plan"].model.model_json_schema()
    assert schema["properties"] == {
        "foo": {"title": "Foo", "type": "integer"},
    }
    assert "foo" not in schema.get("required", [])


def test_overloaded_arg_generated_schema(
    empty_context: BlueskyContext,
):
    def demo_plan(foo: int | str) -> MsgGenerator:
        yield from ()

    empty_context.register_plan(demo_plan)
    schema = empty_context.plans["demo_plan"].model.model_json_schema()
    assert schema["properties"] == {
        "foo": {"title": "Foo", "anyOf": [{"type": "integer"}, {"type": "string"}]}
    }
    assert "foo" in schema.get("required", [])


def test_optional_overloaded_arg_generated_schema(
    empty_context: BlueskyContext,
):
    def demo_plan(foo: int | str | None = None) -> MsgGenerator:
        yield from ()

    empty_context.register_plan(demo_plan)
    schema = empty_context.plans["demo_plan"].model.model_json_schema()
    assert schema["properties"] == {
        "foo": {"title": "Foo", "anyOf": [{"type": "integer"}, {"type": "string"}]}
    }
    assert "foo" not in schema.get("required", [])


def test_explicit_none_arg_generated_schema(
    empty_context: BlueskyContext,
):
    def demo_plan(foo: int | None) -> MsgGenerator:
        yield from ()

    empty_context.register_plan(demo_plan)
    schema = empty_context.plans["demo_plan"].model.model_json_schema()
    assert schema["properties"] == {
        "foo": {
            "title": "Foo",
            "anyOf": [{"type": "integer"}, {"type": "null"}],
        }
    }
    assert "foo" in schema.get("required", [])


@dataclass
class StaticDeviceManager:
    devices: dict[str, Any] = field(default_factory=dict)
    build_errors: dict[str, Exception] = field(default_factory=dict)
    connection_errors: dict[str, Exception] = field(default_factory=dict)

    def build_and_connect(
        self,
        *,
        mock: bool = False,
        timeout: float | None = None,
        fixtures: dict[str, Any] | None = None,
    ) -> DeviceConnectResult:
        return self


def test_empty_device_manager(empty_context: BlueskyContext):
    sdm = StaticDeviceManager()
    empty_context.with_device_manager(sdm)

    assert empty_context.devices == {}


def test_single_device_manager(empty_context: BlueskyContext):
    foo = Device(name="foo")
    sdm = StaticDeviceManager(devices={"foo": foo})
    empty_context.with_device_manager(sdm)

    assert empty_context.devices == {"foo": BlueapiDevice(foo)}


def test_device_manager_build_error(empty_context: BlueskyContext):
    exc = ValueError("broken foo")
    sdm = StaticDeviceManager(build_errors={"foo": exc})
    res = empty_context.with_device_manager(sdm)

    assert empty_context.devices == {}
    assert res[1] == {"foo": exc}


def test_device_manager_connection_error(empty_context: BlueskyContext):
    exc = ValueError("broken foo")
    sdm = StaticDeviceManager(connection_errors={"foo": exc})
    res = empty_context.with_device_manager(sdm)

    assert empty_context.devices == {}
    assert res[1] == {"foo": exc}


def test_device_manager_errors_are_combined(empty_context: BlueskyContext):
    exc1 = ValueError("broken foo")
    exc2 = ValueError("disconnected bar")
    sdm = StaticDeviceManager(
        build_errors={"foo": exc1}, connection_errors={"bar": exc2}
    )
    res = empty_context.with_device_manager(sdm)

    assert empty_context.devices == {}
    assert res[1] == {"foo": exc1, "bar": exc2}


def test_mock_passed_to_device_manager(empty_context: BlueskyContext):
    dm = MagicMock(spec=DeviceManager)
    bc = dm.build_and_connect.return_value
    bc.devices = {}
    bc.build_errors = {}
    bc.connection_errors = {}
    empty_context.with_device_manager(dm, mock=True)

    dm.build_and_connect.assert_called_once_with(mock=True, fixtures={})


def test_path_provider_passed_to_device_manager(empty_context: BlueskyContext):
    dm = MagicMock(spec=DeviceManager)
    bc = dm.build_and_connect.return_value
    bc.devices = {}
    bc.build_errors = {}
    bc.connection_errors = {}
    pp = Mock(spec=PathProvider)
    empty_context.path_provider = pp
    empty_context.with_device_manager(dm)

    dm.build_and_connect.assert_called_once_with(
        mock=False, fixtures={"path_provider": pp}
    )


def test_device_manager_environment_source(empty_context: BlueskyContext):
    foo = Device(name="foo")
    stm = StaticDeviceManager(devices={"foo": foo})
    dev_mod = Mock(spec=ModuleType)
    dev_mod.devices = stm
    env = Mock(spec=EnvironmentConfig)
    env.metadata = None
    env.sources = [DeviceManagerSource(module="foo.bar", mock=True)]
    with patch("blueapi.core.context.import_module") as imp_mod:
        imp_mod.side_effect = lambda mod: dev_mod if mod == "foo.bar" else None
        empty_context.with_config(env)
    assert empty_context.devices == {"foo": BlueapiDevice(foo)}


def test_non_device_manager_errors(empty_context: BlueskyContext):
    dev_mod = Mock(spec=ModuleType, name="dev_mod")
    dev_mod.devices = "not-a-device-manager"
    env = Mock(spec=EnvironmentConfig)
    env.metadata = None
    env.sources = [DeviceManagerSource(module="foo.bar", mock=True)]
    with patch("blueapi.core.context.import_module") as imp_mod:
        imp_mod.side_effect = lambda mod: dev_mod if mod == "foo.bar" else None
        with pytest.raises(ValueError, match="not a device manager"):
            empty_context.with_config(env)


def test_setup_without_tiled_not_makes_tiled_inserter():
    config = TiledConfig(enabled=False)
    context = BlueskyContext(
        ApplicationConfig(
            tiled=config,
            env=EnvironmentConfig(metadata=MetadataConfig(instrument="ixx")),
        )
    )
    assert context.tiled_conf is None


def test_setup_default_not_makes_tiled_inserter():
    context = BlueskyContext(ApplicationConfig())
    assert context.tiled_conf is None


@pytest.mark.parametrize("api_key", [None, "foo"])
def test_setup_with_tiled_makes_tiled_inserter(api_key: str | None):
    config = TiledConfig(enabled=True, authentication=api_key)
    context = BlueskyContext(
        ApplicationConfig(
            tiled=config,
            env=EnvironmentConfig(metadata=MetadataConfig(instrument="ixx")),
        )
    )
    assert context.tiled_conf == config


@pytest.mark.parametrize("api_key", [None, "foo"])
def test_must_have_instrument_set_for_tiled(api_key: str | None):
    config = TiledConfig(enabled=True, authentication=api_key)
    with pytest.raises(InvalidConfigError):
        BlueskyContext(
            ApplicationConfig(tiled=config, env=EnvironmentConfig(metadata=None))
        )


def test_must_have_oidc_config_for_tiled():
    config = TiledConfig(enabled=True, authentication=ServiceAccount())
    with pytest.raises(
        InvalidConfigError,
        match="Tiled has been configured but oidc configuration is missing",
    ):
        BlueskyContext(
            ApplicationConfig(
                tiled=config,
                env=EnvironmentConfig(metadata=MetadataConfig(instrument="ixx")),
                oidc=None,
            )
        )


def test_token_url_set_for_tiled(
    mock_authn_server: responses.RequestsMock, oidc_config: OIDCConfig
):
    config = TiledConfig(enabled=True, authentication=ServiceAccount())

    context = BlueskyContext(
        ApplicationConfig(
            tiled=config,
            env=EnvironmentConfig(metadata=MetadataConfig(instrument="ixx")),
            oidc=oidc_config,
        )
    )
    assert context.tiled_conf.authentication.token_url == oidc_config.token_endpoint  # type:ignore
