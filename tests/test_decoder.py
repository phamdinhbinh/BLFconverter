from pathlib import Path

import cantools
import numpy as np
import pytest

from blf_converter.decoder import DBCBinding, DatabaseRegistry


def write_dbc(tmp_path, signals, *, length=8, frame_id=291, extra="", name="Sample"):
    content = f'''VERSION ""
NS_ :
BS_:
BU_: ECU
BO_ {frame_id} {name}: {length} ECU
{signals}
{extra}
'''
    path = tmp_path / f"{name}.dbc"
    path.write_text(content)
    return path


def payload_matrix(encoded, width=64):
    result = np.zeros((len(encoded), width), dtype=np.uint8)
    for index, data in enumerate(encoded):
        result[index, :len(data)] = np.frombuffer(data, dtype=np.uint8)
    return result


@pytest.mark.parametrize("start,size,endian,signed,length", [
    (0, 8, 1, False, 8), (3, 12, 1, False, 8),
    (7, 24, 1, True, 8), (8, 32, 1, True, 8),
    (0, 64, 1, False, 8), (0, 64, 1, True, 8),
    (3, 64, 1, False, 16), (3, 64, 1, True, 16),
    (7, 8, 0, False, 8), (5, 22, 0, True, 8),
    (7, 64, 0, False, 8), (7, 64, 0, True, 8),
    (4, 64, 0, False, 16), (4, 64, 0, True, 16),
    (55, 8, 0, False, 8), (511, 8, 0, False, 64),
])
def test_matches_cantools_at_boundaries(tmp_path, start, size, endian, signed, length):
    sign = "-" if signed else "+"
    path = write_dbc(tmp_path, f' SG_ Value : {start}|{size}@{endian}{sign} (1,0) [0|0] "unit" ECU', length=length)
    message = cantools.database.load_file(str(path)).messages[0]
    if signed:
        values = [-(1 << (size - 1)), -1, 0, (1 << (size - 1)) - 1]
    else:
        values = [0, 1, (1 << size) // 2, (1 << size) - 1]
    encoded = [message.encode({"Value": value}, scaling=False, strict=False) for value in values]
    # Also test a tightly packed payload array so extraction near its end must
    # use the fallback when a power-of-two view would exceed the payload width.
    payloads = payload_matrix(encoded, width=length)
    groups = DatabaseRegistry([DBCBinding(path)]).decode(1, 291, False, np.arange(len(values)), payloads)
    expected = [message.decode(data, decode_choices=False)["Value"] for data in encoded]
    np.testing.assert_array_equal(groups[0].signals[0].samples, expected)
    assert groups[0].signals[0].unit == "unit"
    assert groups[0].signals[0].name == "CAN1.Sample.Value"


def test_scaling_signed_and_enum_stays_numeric(tmp_path):
    path = write_dbc(tmp_path, ''' SG_ Signed : 0|16@1- (0.1,-50) [-4000|4000] "deg" ECU
 SG_ State : 16|8@1+ (1,0) [0|255] "" ECU''', extra='VAL_ 291 State 0 "Off" 1 "On";')
    message = cantools.database.load_file(str(path)).messages[0]
    encoded = [message.encode({"Signed": -100, "State": 1}, scaling=False),
               message.encode({"Signed": 250, "State": 0}, scaling=False)]
    group = DatabaseRegistry([DBCBinding(path)]).decode(2, 291, False, np.array([0., 1.]), payload_matrix(encoded))[0]
    values = {signal.name.rsplit(".", 1)[-1]: signal for signal in group.signals}
    np.testing.assert_allclose(values["Signed"].samples, [-60., -25.])
    np.testing.assert_array_equal(values["State"].samples, [1, 0])
    assert values["State"].samples.dtype.kind == "u"
    assert "1=On" in values["State"].comment


@pytest.mark.parametrize("size,endian,start", [(32, 1, 0), (32, 0, 7), (64, 1, 0), (64, 0, 7)])
def test_ieee_floats(tmp_path, size, endian, start):
    path = write_dbc(tmp_path, f' SG_ Value : {start}|{size}@{endian}+ (1,0) [0|0] "V" ECU',
                     extra=f'SIG_VALTYPE_ 291 Value : {1 if size == 32 else 2};')
    message = cantools.database.load_file(str(path)).messages[0]
    expected = [0., -1.25, 1234.5]
    encoded = [message.encode({"Value": value}, strict=False) for value in expected]
    actual = DatabaseRegistry([DBCBinding(path)]).decode(1, 291, False, np.arange(3.), payload_matrix(encoded))[0].signals[0].samples
    np.testing.assert_allclose(actual, expected)
    assert actual.dtype.kind == "f"


def test_nested_multiplexing_does_not_invent_inactive_samples(tmp_path):
    path = write_dbc(tmp_path, ''' SG_ Mode M : 0|4@1+ (1,0) [0|15] "" ECU
 SG_ SubMode m1M : 4|4@1+ (1,0) [0|15] "" ECU
 SG_ Always : 8|8@1+ (1,0) [0|255] "" ECU
 SG_ Branch m1 : 16|8@1+ (1,0) [0|255] "" ECU
 SG_ Nested m2 : 24|8@1+ (1,0) [0|255] "" ECU
 SG_ Other m3 : 32|8@1+ (1,0) [0|255] "" ECU''', extra='''SG_MUL_VAL_ 291 SubMode Mode 1-1;
SG_MUL_VAL_ 291 Branch Mode 1-1;
SG_MUL_VAL_ 291 Nested SubMode 2-2;
SG_MUL_VAL_ 291 Other Mode 3-3;''')
    message = cantools.database.load_file(str(path)).messages[0]
    records = [dict(Mode=1, SubMode=2, Always=7, Branch=11, Nested=42),
               dict(Mode=3, Always=8, Other=99),
               dict(Mode=1, SubMode=2, Always=9, Branch=12, Nested=43)]
    encoded = [message.encode(record) for record in records]
    groups = DatabaseRegistry([DBCBinding(path)]).decode(1, 291, False, np.array([0., 1., 2.]), payload_matrix(encoded))
    decoded = {signal.name.rsplit(".", 1)[-1]: (group.timestamps, signal.samples) for group in groups for signal in group.signals}
    np.testing.assert_array_equal(decoded["Always"][1], [7, 8, 9])
    np.testing.assert_array_equal(decoded["Nested"][0], [0, 2])
    np.testing.assert_array_equal(decoded["Nested"][1], [42, 43])
    np.testing.assert_array_equal(decoded["Other"][0], [1])
    np.testing.assert_array_equal(decoded["Other"][1], [99])


def test_channel_bindings_override_wildcards_and_short_frames_are_counted(tmp_path):
    default = write_dbc(tmp_path, ' SG_ Value : 0|8@1+ (1,0) [0|255] "" ECU', name="Default")
    bus = write_dbc(tmp_path, ' SG_ Value : 0|8@1+ (2,0) [0|510] "" ECU', name="Specific")
    registry = DatabaseRegistry([DBCBinding(default), DBCBinding(bus, 2)])
    payloads = np.zeros((2, 64), dtype=np.uint8)
    payloads[:, 0] = [3, 4]
    lengths = np.array([8, 7])
    groups = registry.decode(2, 291, False, np.array([0., 1.]), payloads, lengths)
    assert groups[0].message_name == "Specific"
    np.testing.assert_array_equal(groups[0].signals[0].samples, [6])
    assert registry.truncated_frames == 1
    assert registry.decoded_frames == 1
    assert registry.decode(2, 291, True, np.arange(2.), payloads) == []
    assert registry.unknown_frames == 2


def test_conflicting_databases_fail_with_actionable_message(tmp_path):
    one = write_dbc(tmp_path, ' SG_ Value : 0|8@1+ (1,0) [0|255] "" ECU', name="One")
    two = write_dbc(tmp_path, ' SG_ Value : 0|8@1+ (1,0) [0|255] "" ECU', name="Two")
    with pytest.raises(ValueError, match="assign each DBC"):
        DatabaseRegistry([DBCBinding(one), DBCBinding(two)])


def test_standard_and_extended_identifiers_are_distinct(tmp_path):
    one = write_dbc(tmp_path, ' SG_ Value : 0|8@1+ (1,0) [0|255] "" ECU', name="Standard")
    two = write_dbc(tmp_path, ' SG_ Value : 0|8@1+ (3,0) [0|765] "" ECU', frame_id=0x80000123, name="Extended")
    registry = DatabaseRegistry([DBCBinding(one), DBCBinding(two)])
    payloads = np.zeros((1, 8), dtype=np.uint8)
    payloads[0, 0] = 7
    standard = registry.decode(1, 291, False, np.array([0.]), payloads)[0]
    extended = registry.decode(1, 291, True, np.array([0.]), payloads)[0]
    assert standard.message_name == "Standard"
    np.testing.assert_array_equal(extended.signals[0].samples, [21])


@pytest.mark.parametrize("second_id,second_extended,second_label", [
    (292, False, "Shared_0x124_STD"),
    (0x80000123, True, "Shared_0x123_EXT"),
])
def test_duplicate_message_names_get_distinct_output_channels(
    tmp_path, second_id, second_extended, second_label,
):
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    first = write_dbc(first_dir, ' SG_ Value : 0|8@1+ (1,0) [0|255] "" ECU',
                      name="Shared")
    second = write_dbc(second_dir, ' SG_ Value : 0|8@1+ (2,0) [0|510] "" ECU',
                       frame_id=second_id, name="Shared")
    # A wildcard source and a channel-specific source can both apply to CAN2.
    registry = DatabaseRegistry([DBCBinding(first), DBCBinding(second, 2)])
    payloads = np.zeros((1, 8), dtype=np.uint8)
    payloads[0, 0] = 9
    one = registry.decode(2, 291, False, np.array([0.]), payloads)[0]
    two = registry.decode(2, second_id & 0x1fffffff, second_extended,
                          np.array([1.]), payloads)[0]
    assert one.message_name == "Shared_0x123_STD"
    assert two.message_name == second_label
    assert one.signals[0].name == "CAN2.Shared_0x123_STD.Value"
    assert two.signals[0].name == f"CAN2.{second_label}.Value"
    assert one.signals[0].name != two.signals[0].name
    np.testing.assert_array_equal(one.signals[0].samples, [9])
    np.testing.assert_array_equal(two.signals[0].samples, [18])
    assert len(registry.warnings) == 1
