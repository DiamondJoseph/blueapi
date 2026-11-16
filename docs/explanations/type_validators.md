# Type Validators

When the `context` loads available plans, it iterates through the type signature and replaces any reference to a bluesky protocol with a new class that extends the original type. Defining a class validator on this new type allows it to check that the string being deserialized is the ID of a device of the correct type.

These new intermediate types are used only in the deserialization process. The object returned from validator method is not checked by pydantic so it can be the actual instance and the plan never sees the runtime generated reference type, only the type it was expecting.

:::{note}
This uses the fact that the new types generated at runtime have access to the context that required them via their closure. This circumvents the usual problem of pydantic validation not being able to access external state when validating or deserializing.
:::

```python
def my_plan(a: int, b: Readable) -> Plan:
    ...

# Becomes

class MyPlanModel(BaseModel):
    a: int
    b: Reference[Readable]
```

This also allows `Readable` to be placed at various type levels. For example:
```python
def my_weird_plan(
    a: Readable,
    b: List[Readable],
    c: Dict[str, Readable],
    d: List[List[Readable]],
    e: List[Dict[str, Set[Readable]]]) -> Plan:
    ...
```

## Type Arguments of Devices

An annotation can restrict the values a device works with, as well as the kind of device. `Movable[int]` asks for a device that can be set to an `int`. If the class of a device declares type arguments for the annotated type, for example `class Shutter(StandardReadable, Movable[ShutterState])`, blueapi compares them with the annotation when it looks the device up. A device whose class declares none is not checked.

The comparison follows the variance of the annotated type:

- `Movable` consumes the value it is set to, so it is contravariant. A `Movable[int]` parameter accepts a device declared as `Movable[int]` or as something wider, such as `Movable[object]`. It rejects one declared as `Movable[bool]`, which cannot be set to every `int`.
- For any other type, the device must declare the same type argument or a subclass of it. A `Readable[int]` parameter accepts a device declared as `Readable[bool]`.

`Movable` with no type arguments accepts any `Movable` device.

`Any` is not a way to switch the check off. `Movable[Any]` says that the plan may set the device to anything, so it accepts only a device that can be set to anything: one declared as `Movable[Any]` or `Movable[object]`, or one that declares no type argument. For a type that only produces values the opposite holds, so `Readable[Any]` accepts any `Readable` device.

A device that declares `Any` itself, such as `Movable[Any]`, is accepted whatever type argument is asked for.

The schema that blueapi publishes for a plan lists, for each device parameter, only the devices that the parameter accepts.

### Unions

Where a union is written changes what the annotation asks for:

```python
def set_either(mover: Movable[int] | Movable[str]) -> MsgGenerator[None]: ...


def set_both(mover: Movable[int | str]) -> MsgGenerator[None]: ...
```

- `Movable[int] | Movable[str]` asks for a device that can be set to an `int`, or for one that can be set to a `str`. A device declared as `Movable[int]` is accepted.
- `Movable[int | str]` asks for a single device that can be set to either. A device declared as `Movable[int]` is rejected, because the plan may set it to a `str`. A device declared as `Movable[int | str]` or `Movable[object]` is accepted.

When a device is the right kind but has the wrong type arguments, the error says what it was declared as:

```text
Device shutter (my_beamline.devices.Shutter) is not of type bluesky.protocols.Movable[int | str], it is a bluesky.protocols.Movable[int]
```
