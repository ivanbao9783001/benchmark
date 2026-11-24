"""Physical NPU IDs versus the logical IDs a borrowed container exposes.

Placement, claims and occupancy probes always use host physical IDs. Only the
selection handed to the container runtime (``--device-ids`` and
``ASCEND_RT_VISIBLE_DEVICES``) uses container logical IDs. The mapping cannot be
inferred from device order: it must come from a runtime probe.
"""
import re


def parse_chip_logic_map(text):
    """Parse ``npu-smi info -m`` rows into ``{physical_npu: chip_logic_id}``.

    Rows without a numeric logic ID (for example ``Mcu``) are ignored. Conflicting
    logic IDs for one NPU make the whole answer unusable.
    """
    observed = {}
    for line in str(text).splitlines():
        parts = line.split()
        if len(parts) < 3 or not parts[0].isdigit() or not parts[1].isdigit():
            continue
        if not parts[2].isdigit():
            continue
        npu, logic = int(parts[0]), int(parts[2])
        if npu in observed and observed[npu] != logic:
            raise ValueError('ambiguous chip logic map')
        observed[npu] = logic
    return observed


def validate_device_map(mapping, devices):
    """Normalize a declared map and require it to describe exactly ``devices``."""
    if not isinstance(mapping, dict) or not mapping:
        raise ValueError('device_id_map must be a non-empty physical-to-logical map')
    if any(not isinstance(key, str) or not re.fullmatch(r'0|[1-9][0-9]*', key)
           or type(value) is not int or value < 0 for key, value in mapping.items()):
        raise ValueError('device_id_map requires canonical physical IDs and non-negative logical IDs')
    if sorted(mapping.values()) != list(range(len(mapping))):
        raise ValueError('device_id_map logical IDs must be unique and contiguous from zero')
    resolved = {int(key): value for key, value in mapping.items()}
    if set(resolved) != set(devices):
        raise ValueError('device_id_map keys must exactly cover the devices placed on this node')
    return resolved


def device_map_mismatches(declared, observed):
    """Physical IDs whose observed logic ID differs from the declared one."""
    return {int(key): value for key, value in declared.items()
            if observed.get(int(key)) != value}
