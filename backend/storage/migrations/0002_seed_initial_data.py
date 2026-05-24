"""Seed initial tenants, UO mappings, and group-tenant mappings."""

from django.db import migrations


def seed_data(apps, schema_editor):
    Tenant = apps.get_model("storage", "Tenant")
    UOMapping = apps.get_model("storage", "UOMapping")
    GroupTenantMapping = apps.get_model("storage", "GroupTenantMapping")

    # Create tenants
    tenants = {}
    for code, name, structure, prefix in [
        ("NFFADI", "NFFA-DI", "NFFADI", "nffa-di"),
        ("LADE", "LADE Laboratory", "", "lade"),
        ("LAME", "LAME Laboratory", "", "lame"),
        ("LAGE", "LAGE Laboratory", "", "lage"),
        ("PRP", "PRP", "", "prp"),
    ]:
        t = Tenant.objects.create(
            code=code,
            name=name,
            microservice_structure=structure,
            bucket_name_prefix=prefix,
        )
        tenants[code] = t

    # UO mappings for NFFADI
    nffadi = tenants["NFFADI"]
    uo_data = [
        ("CNR - Istituto Officina dei Materiali - Trieste", "cnr-iom.ts"),
        ("CNR - Istituto di Microelettronica e Microsistemi - Catania", "cnr-imm.ct"),
        (
            "CNR - Istituto per lo Studio dei Materiali Nanostrutturati - Bologna",
            "cnr-ismn.bo",
        ),
        ("CNR - Istituto di Fotonica e Nanotecnologie - Milano", "cnr-ifn.mi"),
        ("CNR - Istituto di Fotonica e Nanotecnologie - Trento", "cnr-ifn.tn"),
        ("CNR - Istituto di Nanotecnologia - Lecce", "cnr-nanotec.le"),
        (
            "CNR - Institute for SuPerconductors, INnovative materials, and devices - Napoli",
            "cnr-spin.na",
        ),
        ("CNR - Istituto di Struttura della Materia - Roma", "cnr-ism.rm"),
        ("AREA - Istituto Ricerca e Innovazione Tecnologica (Trieste)", "area-rit"),
        ("POLIMI - POLIFAB (Milano)", "polifab"),
        ('UNIMI - Dipartimento di Fisica "Aldo Pontremoli" (Milano)', "unimi"),
    ]
    for institution_name, uo_code in uo_data:
        UOMapping.objects.create(
            tenant=nffadi,
            institution_name=institution_name,
            uo_code=uo_code,
        )

    # Authentik group → tenant mappings
    group_data = [
        ("nffa-di-users", "NFFADI"),
        ("lade-users", "LADE"),
        ("lame-users", "LAME"),
        ("lage-users", "LAGE"),
        ("prp-users", "PRP"),
    ]
    for group, tenant_code in group_data:
        GroupTenantMapping.objects.create(
            authentik_group=group,
            tenant=tenants[tenant_code],
        )


def reverse_seed(apps, schema_editor):
    Tenant = apps.get_model("storage", "Tenant")
    Tenant.objects.filter(code__in=["NFFADI", "LADE", "LAME", "LAGE", "PRP"]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("storage", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_data, reverse_seed),
    ]
