"""Shared tenant access policy helpers."""

WRITE_CAPABLE_ROLES = {"rw", "admin"}
NFFADI_STRUCTURE = "NFFADI"
NFFADI_AUTHENTIK_GROUP = "nffa-di-users"


def is_write_capable(role):
    return role in WRITE_CAPABLE_ROLES


def structure_name(tenant):
    return tenant.rgwsquared_structure or tenant.code


def is_nffadi_tenant(tenant):
    return structure_name(tenant).upper() == NFFADI_STRUCTURE


def suggested_group_name(structure, role):
    slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in structure)
    slug = "-".join(part for part in slug.split("-") if part)
    if structure.upper() == NFFADI_STRUCTURE:
        return NFFADI_AUTHENTIK_GROUP
    return f"{slug}-users" if role == "rw" else f"{slug}-ext"


def is_valid_nffadi_mapping(mapping):
    return (
        is_nffadi_tenant(mapping.tenant)
        and mapping.role == "rw"
        and mapping.authentik_group == NFFADI_AUTHENTIK_GROUP
    )
