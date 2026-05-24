import os
import time
from io import StringIO
from unittest.mock import patch

import requests
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import Resolver404, resolve
from rest_framework.test import APIRequestFactory, force_authenticate

from storage.middleware import OAuthExceptionRedirectMiddleware
from storage.pipeline import extract_tenant_info
from storage.models import (
    Bucket,
    BucketPermission,
    FileUploadRecord,
    GroupTenantMapping,
    Tenant,
    TenantMembership,
    UOMapping,
    User,
)
from storage.services.rgw_squared import RGWSquaredClient, RGWSquaredError
from storage.services.s3_ops import fetch_mgmt_keys
from storage.services.sync_service import refresh_local_cache
from storage.views.admin import (
    AdminLoginThrottle,
    admin_group_mappings,
    admin_login,
    admin_membership_files,
    admin_sync_refresh,
    admin_sync_upload_csv,
    admin_tenant_activation,
    admin_users,
)
from storage.views.buckets import BucketViewSet


class FakeHTTPResponse:
    def __init__(self, status_code=200, json_data=None, text="", headers=None):
        self.status_code = status_code
        self._json_data = json_data
        self.text = text
        self.headers = headers or {}

    def json(self):
        if self._json_data is None:
            raise requests.exceptions.JSONDecodeError("bad json", self.text, 0)
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(self.text)


class RGWSquaredClientTests(TestCase):
    def rgw_client(self):
        client = RGWSquaredClient("http://rgw", "admin", "secret")
        client._token = "token"
        client._token_expires_at = time.time() + 3600
        return client

    @patch("storage.services.rgw_squared.requests.post")
    def test_v2_paths_and_payloads(self, post):
        post.return_value = FakeHTTPResponse(json_data={"res": []})
        client = self.rgw_client()

        client.list_structures()
        client.upload_csv("dGVzdA==")
        client.update_structure("NFFADI", update_from_ext=True)

        self.assertEqual(
            [call.args[0] for call in post.call_args_list],
            [
                "http://rgw/s3struct/structureList",
                "http://rgw/s3structnffadi/csvUpload",
                "http://rgw/s3struct/structureUpdate",
            ],
        )
        self.assertEqual(post.call_args_list[0].kwargs["json"], {})
        self.assertEqual(post.call_args_list[1].kwargs["json"], {"content": "dGVzdA=="})
        self.assertEqual(
            post.call_args_list[2].kwargs["json"],
            {"updateFromExt": True, "structure": "NFFADI"},
        )

    @patch("storage.services.rgw_squared.requests.post")
    def test_plain_text_500_body_is_preserved(self, post):
        post.return_value = FakeHTTPResponse(
            status_code=500,
            text='The call "bucketCreate" from webservice "s3struct" has failed.',
        )

        with self.assertRaises(RGWSquaredError) as ctx:
            self.rgw_client().create_bucket("NFFADI", "demo")

        self.assertIn("bucketCreate", str(ctx.exception))


class EnsureSuperuserCommandTests(TestCase):
    def call_command(self, env, debug=True):
        out = StringIO()
        with override_settings(DEBUG=debug):
            with patch.dict(os.environ, env, clear=False):
                call_command("ensure_superuser", stdout=out)
        return out.getvalue()

    def test_creates_local_admin_from_env(self):
        self.call_command(
            {
                "DJANGO_SUPERUSER_USERNAME": "admin",
                "DJANGO_SUPERUSER_PASSWORD": "secret-password",
                "DJANGO_SUPERUSER_EMAIL": "admin@example.com",
            }
        )

        user = User.objects.get(username="admin")
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.is_active)
        self.assertTrue(user.is_approved)
        self.assertEqual(user.email, "admin@example.com")
        self.assertEqual(user.external_id, "admin-local")
        self.assertEqual(user.idp_source, "local")
        self.assertTrue(user.check_password("secret-password"))

    def test_repairs_existing_admin_and_rotates_password(self):
        user = User.objects.create_user(
            username="admin",
            email="old@example.com",
            external_id="admin-local",
            password="old-password",
            is_staff=False,
            is_superuser=False,
            is_active=False,
            is_approved=False,
        )

        self.call_command(
            {
                "DJANGO_SUPERUSER_USERNAME": "admin",
                "DJANGO_SUPERUSER_PASSWORD": "new-password",
                "DJANGO_SUPERUSER_EMAIL": "admin@example.com",
            }
        )

        user.refresh_from_db()
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.is_active)
        self.assertTrue(user.is_approved)
        self.assertEqual(user.email, "admin@example.com")
        self.assertEqual(user.idp_source, "local")
        self.assertTrue(user.check_password("new-password"))
        self.assertFalse(user.check_password("old-password"))

    def test_skips_when_completely_unconfigured(self):
        output = self.call_command(
            {
                "DJANGO_SUPERUSER_USERNAME": "",
                "DJANGO_SUPERUSER_PASSWORD": "",
                "DJANGO_SUPERUSER_EMAIL": "",
            }
        )

        self.assertIn("not configured", output)
        self.assertFalse(User.objects.filter(username="admin").exists())

    def test_non_debug_partial_configuration_fails(self):
        with self.assertRaises(CommandError):
            self.call_command(
                {
                    "DJANGO_SUPERUSER_USERNAME": "admin",
                    "DJANGO_SUPERUSER_PASSWORD": "",
                    "DJANGO_SUPERUSER_EMAIL": "admin@example.com",
                },
                debug=False,
            )


class AdminMembershipUsersTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.admin = User.objects.create_user(
            username="admin-user",
            email="admin-user@example.com",
            external_id="sub-admin-user",
            password="admin-password",
            is_staff=True,
            is_superuser=True,
            is_approved=True,
        )
        self.tenant_a = Tenant.objects.create(
            code="TENANT_A",
            name="Tenant A",
            rgwsquared_structure="TENANT_A",
        )
        self.tenant_b = Tenant.objects.create(
            code="TENANT_B",
            name="Tenant B",
            rgwsquared_structure="TENANT_B",
        )
        self.user = User.objects.create_user(
            username="researcher",
            email="researcher@example.com",
            external_id="sub-researcher",
        )
        self.membership_a = TenantMembership.objects.create(
            user=self.user,
            tenant=self.tenant_a,
            ceph_username="researcher-a",
            role="rw",
        )
        self.membership_b = TenantMembership.objects.create(
            user=self.user,
            tenant=self.tenant_b,
            ceph_username="researcher-b",
            role="ro",
        )
        self.bucket_a = Bucket.objects.create(
            name="tenant-a-bucket",
            display_name="bucket-a",
            tenant=self.tenant_a,
            owner=self.user,
            bucket_type=Bucket.LOCAL,
        )
        self.bucket_b = Bucket.objects.create(
            name="tenant-b-bucket",
            display_name="bucket-b",
            tenant=self.tenant_b,
            owner=self.user,
            bucket_type=Bucket.LOCAL,
        )
        FileUploadRecord.objects.create(
            bucket=self.bucket_a,
            uploaded_by=self.user,
            file_key="a-one.txt",
            file_size=100,
        )
        FileUploadRecord.objects.create(
            bucket=self.bucket_b,
            uploaded_by=self.user,
            file_key="b-one.txt",
            file_size=300,
        )
        FileUploadRecord.objects.create(
            bucket=self.bucket_b,
            uploaded_by=self.user,
            file_key="b-two.txt",
            file_size=700,
        )

    def admin_request(self, path):
        request = self.factory.get(path)
        force_authenticate(request, user=self.admin)
        return request

    def test_admin_users_reports_storage_per_membership_tenant(self):
        request = self.admin_request("/api/admin/users/")
        response = admin_users(request)

        self.assertEqual(response.status_code, 200, response.data)
        rows = {
            row["tenant_code"]: row
            for row in response.data
            if row["user_id"] == self.user.id
        }
        self.assertEqual(rows["TENANT_A"]["membership_id"], self.membership_a.id)
        self.assertEqual(rows["TENANT_A"]["file_count"], 1)
        self.assertEqual(rows["TENANT_A"]["total_file_size"], 100)
        self.assertEqual(rows["TENANT_B"]["membership_id"], self.membership_b.id)
        self.assertEqual(rows["TENANT_B"]["file_count"], 2)
        self.assertEqual(rows["TENANT_B"]["total_file_size"], 1000)

    def test_admin_users_tenant_filter_is_membership_scoped(self):
        request = self.admin_request("/api/admin/users/?tenant_code=TENANT_B")
        response = admin_users(request)

        self.assertEqual(response.status_code, 200, response.data)
        tenant_codes = {row["tenant_code"] for row in response.data}
        self.assertEqual(tenant_codes, {"TENANT_B"})

    def test_membership_files_do_not_leak_across_tenants(self):
        request = self.admin_request(
            f"/api/admin/memberships/{self.membership_a.id}/files/"
        )
        response = admin_membership_files(request, self.membership_a.id)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([row["file_key"] for row in response.data], ["a-one.txt"])
        self.assertEqual({row["tenant_code"] for row in response.data}, {"TENANT_A"})

    def test_old_user_files_route_is_removed(self):
        with self.assertRaises(Resolver404):
            resolve("/api/admin/users/1/files/")


class TenantActivationSummaryTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.admin = User.objects.create_user(
            username="activation-admin",
            email="activation-admin@example.com",
            external_id="sub-activation-admin",
            password="admin-password",
            is_staff=True,
            is_superuser=True,
            is_approved=True,
        )

    def admin_request(self):
        request = self.factory.get("/api/admin/tenant-activation/")
        force_authenticate(request, user=self.admin)
        return request

    def activation_rows(self, structures, structure_info):
        class ActivationClient:
            def list_structures(self):
                return structures

            def get_structure_info(self, structure):
                return structure_info.get(structure, {"initialized": True})

        with patch("storage.views.admin._get_sync_client", return_value=ActivationClient()):
            response = admin_tenant_activation(self.admin_request())

        self.assertEqual(response.status_code, 200, response.data)
        return {row["structure"]: row for row in response.data}

    def create_tenant(self, code="LAB"):
        return Tenant.objects.create(
            code=code,
            name=f"{code} Lab",
            rgwsquared_structure=code,
            is_active=True,
        )

    def create_member(self, tenant, username="researcher", role="rw", uo_code=""):
        user = User.objects.create_user(
            username=username,
            email=f"{username}@example.com",
            external_id=f"sub-{username}",
        )
        return TenantMembership.objects.create(
            user=user,
            tenant=tenant,
            ceph_username=username,
            role=role,
            uo_code=uo_code,
        )

    def test_uninitialized_rgwsquared_structure_is_not_fully_active(self):
        rows = self.activation_rows(["LAB"], {"LAB": {"initialized": False}})

        row = rows["LAB"]
        self.assertFalse(row["fully_active"])
        self.assertFalse(row["has_tenant"])
        self.assertFalse(row["initialized"])

    def test_initialized_structure_without_local_tenant_is_not_fully_active(self):
        rows = self.activation_rows(["LAB"], {"LAB": {"initialized": True}})

        row = rows["LAB"]
        self.assertFalse(row["fully_active"])
        self.assertFalse(row["has_tenant"])
        self.assertTrue(row["initialized"])

    def test_local_tenant_without_group_mapping_is_not_fully_active(self):
        tenant = self.create_tenant()
        self.create_member(tenant, role="rw")

        rows = self.activation_rows(["LAB"], {"LAB": {"initialized": True}})

        row = rows["LAB"]
        self.assertFalse(row["fully_active"])
        self.assertTrue(row["has_tenant"])
        self.assertFalse(row["has_group_mapping"])

    def test_activation_matches_tenant_by_rgwsquared_structure(self):
        tenant = self.create_tenant(code="LOCAL_CODE")
        tenant.rgwsquared_structure = "LAB"
        tenant.save(update_fields=["rgwsquared_structure"])

        rows = self.activation_rows(["LAB"], {"LAB": {"initialized": True}})

        row = rows["LAB"]
        self.assertTrue(row["has_tenant"])
        self.assertEqual(row["tenant_code"], "LOCAL_CODE")
        self.assertEqual(row["structure"], "LAB")

    def test_nffadi_requires_exact_single_group_mapping(self):
        tenant = self.create_tenant(code="NFFADI")
        GroupTenantMapping.objects.create(
            tenant=tenant,
            authentik_group="nffadi-users",
            role="rw",
        )

        rows = self.activation_rows(["NFFADI"], {"NFFADI": {"initialized": True}})

        row = rows["NFFADI"]
        self.assertFalse(row["fully_active"])
        self.assertFalse(row["group_mapping_ready"])
        self.assertEqual(row["required_group_name"], "nffa-di-users")
        self.assertEqual(row["role_source"], "rgwsquared")

    def test_nffadi_exact_group_mapping_is_fully_active_without_uo_requirement(self):
        tenant = self.create_tenant(code="NFFADI")
        GroupTenantMapping.objects.create(
            tenant=tenant,
            authentik_group="nffa-di-users",
            role="rw",
        )

        rows = self.activation_rows(["NFFADI"], {"NFFADI": {"initialized": True}})

        row = rows["NFFADI"]
        self.assertTrue(row["fully_active"])
        self.assertTrue(row["group_mapping_ready"])
        self.assertEqual(row["suggested_ro_group"], "nffa-di-users")

    def test_group_mapped_tenant_without_uo_requirement_is_fully_active(self):
        tenant = self.create_tenant()
        self.create_member(tenant, role="rw")
        GroupTenantMapping.objects.create(
            tenant=tenant,
            authentik_group="lab-users",
            role="rw",
        )

        rows = self.activation_rows(["LAB"], {"LAB": {"initialized": True}})

        row = rows["LAB"]
        self.assertTrue(row["fully_active"])
        self.assertTrue(row["has_group_mapping"])
        self.assertFalse(row["requires_uo_sync"])
        self.assertTrue(row["uo_ready"])

    def test_uo_required_tenant_missing_write_capable_uo_is_not_fully_active(self):
        tenant = self.create_tenant()
        self.create_member(tenant, role="rw", uo_code="")
        self.create_member(tenant, username="reader", role="ro", uo_code="")
        GroupTenantMapping.objects.create(
            tenant=tenant,
            authentik_group="lab-users",
            role="rw",
        )
        UOMapping.objects.create(
            tenant=tenant,
            uo_code="lab-unit",
            institution_name="Lab Unit",
        )

        rows = self.activation_rows(["LAB"], {"LAB": {"initialized": True}})

        row = rows["LAB"]
        self.assertFalse(row["fully_active"])
        self.assertTrue(row["requires_uo_sync"])
        self.assertFalse(row["uo_ready"])
        self.assertEqual(row["missing_uo_count"], 1)

    def test_admin_sync_refresh_calls_rgwsquared_cache_refresh(self):
        tenant = self.create_tenant()
        request = self.factory.post(
            "/api/admin/sync/refresh/",
            {"structure_code": tenant.code},
            format="json",
        )
        force_authenticate(request, user=self.admin)

        with patch(
            "storage.views.admin.refresh_local_cache",
            return_value={"users_synced": 1, "buckets_synced": 2},
        ) as refresh:
            response = admin_sync_refresh(request)

        self.assertEqual(response.status_code, 200, response.data)
        refresh.assert_called_once_with(tenant)
        self.assertEqual(response.data["users_synced"], 1)
        self.assertEqual(response.data["buckets_synced"], 2)

    def test_uo_required_tenant_with_write_capable_uo_is_fully_active(self):
        tenant = self.create_tenant()
        self.create_member(tenant, role="rw", uo_code="lab-unit")
        self.create_member(tenant, username="reader", role="ro", uo_code="")
        GroupTenantMapping.objects.create(
            tenant=tenant,
            authentik_group="lab-users",
            role="rw",
        )
        UOMapping.objects.create(
            tenant=tenant,
            uo_code="lab-unit",
            institution_name="Lab Unit",
        )

        rows = self.activation_rows(["LAB"], {"LAB": {"initialized": True}})

        row = rows["LAB"]
        self.assertTrue(row["fully_active"])
        self.assertTrue(row["requires_uo_sync"])
        self.assertTrue(row["uo_ready"])
        self.assertEqual(row["missing_uo_count"], 0)


class AdminLoginTests(TestCase):
    def setUp(self):
        cache.clear()
        self.factory = APIRequestFactory()
        self.admin = User.objects.create_user(
            username="admin",
            email="admin@example.com",
            external_id="admin-local",
            password="admin-password",
            is_staff=True,
            is_superuser=True,
            is_approved=True,
        )

    def test_admin_login_returns_jwt_for_staff_user(self):
        request = self.factory.post(
            "/api/admin/login/",
            {"username": "admin", "password": "admin-password"},
            format="json",
        )
        response = admin_login(request)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)
        self.assertEqual(response.data["username"], "admin")

    def test_admin_login_rejects_bad_password(self):
        request = self.factory.post(
            "/api/admin/login/",
            {"username": "admin", "password": "wrong"},
            format="json",
        )
        response = admin_login(request)

        self.assertEqual(response.status_code, 401, response.data)

    def test_admin_login_is_throttled(self):
        with patch.object(
            AdminLoginThrottle, "THROTTLE_RATES", {"admin_login": "2/min"}
        ):
            for _ in range(2):
                request = self.factory.post(
                    "/api/admin/login/",
                    {"username": "admin", "password": "wrong"},
                    format="json",
                    REMOTE_ADDR="203.0.113.10",
                )
                response = admin_login(request)
                self.assertEqual(response.status_code, 401, response.data)

            request = self.factory.post(
                "/api/admin/login/",
                {"username": "admin", "password": "wrong"},
                format="json",
                REMOTE_ADDR="203.0.113.10",
            )
            response = admin_login(request)

            self.assertEqual(response.status_code, 429, response.data)




class OAuthExceptionMiddlewareTests(TestCase):
    def test_auth_state_missing_redirects_to_login_retry(self):
        from social_core.exceptions import AuthStateMissing

        request = APIRequestFactory().get("/api/oauth/complete/authentik/")
        middleware = OAuthExceptionRedirectMiddleware(lambda req: None)
        response = middleware.process_exception(request, AuthStateMissing(None))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/login?auth_error=oauth_state_missing")


class AdminGroupMappingPolicyTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.admin = User.objects.create_user(
            username="group-admin",
            email="group-admin@example.com",
            external_id="sub-group-admin",
            password="admin-password",
            is_staff=True,
            is_superuser=True,
            is_approved=True,
        )
        self.nffadi = Tenant.objects.create(
            code="NFFADI",
            name="NFFA-DI",
            rgwsquared_structure="NFFADI",
        )
        self.orbit = Tenant.objects.create(
            code="ORBIT",
            name="Orbit",
            rgwsquared_structure="ORBIT",
        )

    def post_mapping(self, tenant, group, role="rw"):
        request = self.factory.post(
            "/api/admin/group-mappings/",
            {"tenant_id": tenant.id, "authentik_group": group, "role": role},
            format="json",
        )
        force_authenticate(request, user=self.admin)
        return admin_group_mappings(request)

    def test_nffadi_accepts_only_single_required_group(self):
        response = self.post_mapping(self.nffadi, "nffa-di-users", "rw")
        self.assertEqual(response.status_code, 201, response.data)

        response = self.post_mapping(self.nffadi, "nffa-di-readers", "ro")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("nffa-di-users", response.data["error"])

    def test_non_nffadi_allows_generic_rw_and_ro_groups(self):
        response = self.post_mapping(self.orbit, "orbit-users", "rw")
        self.assertEqual(response.status_code, 201, response.data)
        response = self.post_mapping(self.orbit, "orbit-ext", "ro")
        self.assertEqual(response.status_code, 201, response.data)


@override_settings(
    RGWSQUARED_URL="http://rgw",
    RGWSQUARED_USERNAME="admin",
    RGWSQUARED_PASSWORD="secret",
)
class AuthPipelineTenantPolicyTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="alice",
            email="alice@example.com",
            external_id="sub-alice",
            institution="CNR - Istituto Officina dei Materiali - Trieste",
        )
        self.nffadi = Tenant.objects.create(
            code="NFFADI",
            name="NFFA-DI",
            rgwsquared_structure="NFFADI",
        )
        self.orbit = Tenant.objects.create(
            code="ORBIT",
            name="Orbit",
            rgwsquared_structure="ORBIT",
        )

    def test_nffadi_role_comes_from_rgwsquared_not_group_mapping(self):
        GroupTenantMapping.objects.create(
            tenant=self.nffadi,
            authentik_group="nffa-di-users",
            role="rw",
        )
        UOMapping.objects.create(
            tenant=self.nffadi,
            uo_code="cnr-iom.ts",
            institution_name="CNR - Istituto Officina dei Materiali - Trieste",
        )

        class Client:
            def __init__(self, *args, **kwargs):
                pass

            def list_users(self, structure):
                return ["alice"]

            def get_user_info(self, structure, username):
                return {"ROBuckets": ["NFFADI:proposal-1"], "RWBuckets": []}

            def list_buckets(self, structure):
                return [{"name": "NFFADI:proposal-1", "auto": True}]

        with patch("storage.services.rgw_squared.RGWSquaredClient", Client):
            extract_tenant_info(
                None,
                {},
                {"preferred_username": "alice", "groups": ["nffa-di-users"]},
                user=self.user,
            )

        membership = TenantMembership.objects.get(user=self.user, tenant=self.nffadi)
        self.assertEqual(membership.role, "ro")
        self.assertEqual(membership.uo_code, "")

    def test_partial_multi_tenant_login_keeps_valid_tenant_when_nffadi_missing_rgw_user(self):
        GroupTenantMapping.objects.create(
            tenant=self.nffadi,
            authentik_group="nffa-di-users",
            role="rw",
        )
        GroupTenantMapping.objects.create(
            tenant=self.orbit,
            authentik_group="orbit-users",
            role="rw",
        )

        class Client:
            def __init__(self, *args, **kwargs):
                pass

            def list_users(self, structure):
                return [] if structure == "NFFADI" else ["alice"]

            def get_user_info(self, structure, username):
                return {"ROBuckets": [], "RWBuckets": ["ORBIT:work"]}

            def list_buckets(self, structure):
                return [{"name": "ORBIT:work", "auto": True}]

        with patch("storage.services.rgw_squared.RGWSquaredClient", Client):
            extract_tenant_info(
                None,
                {},
                {"preferred_username": "alice", "groups": ["nffa-di-users", "orbit-users"]},
                user=self.user,
            )

        self.assertFalse(TenantMembership.objects.filter(user=self.user, tenant=self.nffadi).exists())
        orbit_membership = TenantMembership.objects.get(user=self.user, tenant=self.orbit)
        self.assertEqual(orbit_membership.role, "rw")


class AdminCSVUOTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.admin = User.objects.create_user(
            username="csv-admin",
            email="csv-admin@example.com",
            external_id="sub-csv-admin",
            password="admin-password",
            is_staff=True,
            is_superuser=True,
            is_approved=True,
        )
        self.tenant = Tenant.objects.create(
            code="NFFADI",
            name="NFFA-DI",
            rgwsquared_structure="NFFADI",
        )
        UOMapping.objects.create(
            tenant=self.tenant,
            uo_code="cnr-iom.ts",
            institution_name="CNR - Istituto Officina dei Materiali - Trieste",
        )
        self.rw_user = User.objects.create_user(
            username="rwuser",
            email="rwuser@example.com",
            external_id="sub-rwuser",
        )
        self.ro_user = User.objects.create_user(
            username="rouser",
            email="rouser@example.com",
            external_id="sub-rouser",
        )
        self.rw_membership = TenantMembership.objects.create(
            user=self.rw_user,
            tenant=self.tenant,
            ceph_username="rwuser",
            role="rw",
        )
        self.ro_membership = TenantMembership.objects.create(
            user=self.ro_user,
            tenant=self.tenant,
            ceph_username="rouser",
            role="ro",
            uo_code="stale-code",
        )

    def test_csv_assigns_uo_only_to_write_capable_memberships_and_clears_ro(self):
        csv_body = (
            "instrument_scientist_username,instrument_scientist_email,institution\n"
            "rwuser,rwuser@example.com,CNR - Istituto Officina dei Materiali - Trieste\n"
            "rouser,rouser@example.com,CNR - Istituto Officina dei Materiali - Trieste\n"
        )
        upload = SimpleUploadedFile("uo.csv", csv_body.encode("utf-8"), content_type="text/csv")
        request = self.factory.post(
            "/api/admin/sync/upload-csv/",
            {"file": upload},
            format="multipart",
        )
        force_authenticate(request, user=self.admin)

        class Client:
            def upload_csv(self, content):
                return {"ok": True}

        with patch("storage.views.admin._get_sync_client", return_value=Client()):
            response = admin_sync_upload_csv(request)

        self.assertEqual(response.status_code, 200, response.data)
        self.rw_membership.refresh_from_db()
        self.ro_membership.refresh_from_db()
        self.assertEqual(self.rw_membership.uo_code, "cnr-iom.ts")
        self.assertEqual(self.rw_membership.role, "rw")
        self.assertEqual(self.ro_membership.role, "ro")
        self.assertEqual(self.ro_membership.uo_code, "")

class RecordingRGWClient:
    def __init__(self, initialized=True, users=None):
        self.initialized = initialized
        self.users = list(users or [])
        self.calls = []
        self.share_exists_at_update = None
        self.share_probe = None

    def get_structure_info(self, structure):
        self.calls.append(("structureInfo", structure))
        return {
            "initialized": self.initialized,
            "rgwintUser": {"access_key": "AK", "secret_key": "SK"},
        }

    def list_users(self, structure):
        self.calls.append(("userList", structure))
        return list(self.users)

    def create_user(self, structure, user):
        self.calls.append(("userCreate", structure, user))
        self.users.append(user)

    def create_bucket(
        self, structure, bucket_name, rw_permissions=None, ro_permissions=None, tags=None
    ):
        self.calls.append(
            (
                "bucketCreate",
                structure,
                bucket_name,
                rw_permissions or [],
                ro_permissions or [],
            )
        )

    def update_bucket(
        self, structure, bucket_name, rw_permissions=None, ro_permissions=None, tags=None
    ):
        if self.share_probe:
            bucket, user = self.share_probe
            self.share_exists_at_update = BucketPermission.objects.filter(
                bucket=bucket, user=user
            ).exists()
        self.calls.append(
            (
                "bucketUpdate",
                structure,
                bucket_name,
                rw_permissions or [],
                ro_permissions or [],
            )
        )

    def delete_bucket(self, structure, bucket_name):
        self.calls.append(("bucketDelete", structure, bucket_name))


@override_settings(
    RGWSQUARED_URL="http://rgw",
    RGWSQUARED_USERNAME="admin",
    RGWSQUARED_PASSWORD="secret",
)
class BucketLifecycleTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.view_create = BucketViewSet.as_view({"post": "create"})
        self.view_delete = BucketViewSet.as_view({"delete": "destroy"})
        self.view_shares = BucketViewSet.as_view({"post": "shares"})
        self.tenant, _ = Tenant.objects.update_or_create(
            code="NFFADI",
            defaults={
                "name": "NFFA-DI",
                "rgwsquared_structure": "NFFADI",
            },
        )
        self.owner = self.create_user("owner")
        self.target = self.create_user("target")
        self.owner_membership = TenantMembership.objects.create(
            user=self.owner,
            tenant=self.tenant,
            ceph_username="owner",
            role="rw",
            uo_code="cnr-iom.ts",
        )
        TenantMembership.objects.create(
            user=self.target,
            tenant=self.tenant,
            ceph_username="target",
            role="ro",
            uo_code="cnr-iom.ts",
        )

    def create_user(self, username):
        return User.objects.create_user(
            username=username,
            email=f"{username}@example.com",
            external_id=f"sub-{username}",
        )

    def authenticated_request(self, method, path, user, data=None):
        request = getattr(self.factory, method)(
            path,
            data or {},
            format="json",
            HTTP_X_TENANT_ID=str(self.tenant.id),
        )
        force_authenticate(request, user=user)
        return request

    def local_bucket(self):
        bucket = Bucket.objects.create(
            name="owner-cnr-iom-ts-existing",
            tenant=self.tenant,
            owner=self.owner,
            bucket_type=Bucket.LOCAL,
            is_deletable=True,
            display_name="existing",
        )
        BucketPermission.objects.create(
            bucket=bucket,
            user=self.owner,
            permission="owner",
            source="local",
        )
        return bucket

    def test_create_calls_rgwsquared_before_persisting_metadata(self):
        rgw = RecordingRGWClient(users=[])
        request = self.authenticated_request(
            "post", "/api/buckets/", self.owner, {"name": "project-1"}
        )

        with patch("storage.views.buckets._get_rgw_client", return_value=rgw):
            response = self.view_create(request)

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(
            rgw.calls,
            [
                ("structureInfo", "NFFADI"),
                ("userList", "NFFADI"),
                ("userCreate", "NFFADI", "owner"),
                (
                    "bucketCreate",
                    "NFFADI",
                    "owner-cnr-iom-ts-project-1",
                    ["owner"],
                    [],
                ),
            ],
        )
        bucket = Bucket.objects.get(name="owner-cnr-iom-ts-project-1")
        self.assertTrue(
            BucketPermission.objects.filter(
                bucket=bucket,
                user=self.owner,
                permission="owner",
                source="local",
            ).exists()
        )

    def test_create_duplicate_uses_display_name_in_error(self):
        Bucket.objects.create(
            name="owner-cnr-iom-ts-project-1",
            tenant=self.tenant,
            owner=self.owner,
            bucket_type=Bucket.LOCAL,
            is_deletable=True,
            display_name="project-1",
        )
        request = self.authenticated_request(
            "post", "/api/buckets/", self.owner, {"name": "project-1"}
        )

        response = self.view_create(request)

        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data["error"], "Bucket 'project-1' already exists")
        self.assertNotIn("owner-cnr-iom-ts-project-1", response.data["error"])

    def test_rgwsquared_duplicate_create_uses_display_name_in_error(self):
        class DuplicateRGWClient(RecordingRGWClient):
            def create_bucket(
                self,
                structure,
                bucket_name,
                rw_permissions=None,
                ro_permissions=None,
                tags=None,
            ):
                self.calls.append(("bucketCreate", structure, bucket_name))
                raise RGWSquaredError(f"Bucket {bucket_name} already exists")

        rgw = DuplicateRGWClient(users=["owner"])
        request = self.authenticated_request(
            "post", "/api/buckets/", self.owner, {"name": "project-dup"}
        )

        with patch("storage.views.buckets._get_rgw_client", return_value=rgw):
            response = self.view_create(request)

        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data["error"], "Bucket 'project-dup' already exists")
        self.assertFalse(Bucket.objects.filter(display_name="project-dup").exists())

    def test_uninitialized_structure_blocks_bucket_create(self):
        rgw = RecordingRGWClient(initialized=False, users=["owner"])
        request = self.authenticated_request(
            "post", "/api/buckets/", self.owner, {"name": "project-2"}
        )

        with patch("storage.views.buckets._get_rgw_client", return_value=rgw):
            response = self.view_create(request)

        self.assertEqual(response.status_code, 503, response.data)
        self.assertEqual(rgw.calls, [("structureInfo", "NFFADI")])
        self.assertFalse(Bucket.objects.filter(display_name="project-2").exists())

    def test_delete_calls_rgwsquared_before_deleting_metadata(self):
        bucket = self.local_bucket()
        rgw = RecordingRGWClient(users=["owner"])
        request = self.authenticated_request(
            "delete", f"/api/buckets/{bucket.id}/", self.owner
        )

        with patch("storage.views.buckets._get_rgw_client", return_value=rgw):
            response = self.view_delete(request, pk=bucket.id)

        self.assertEqual(response.status_code, 204, response.data)
        self.assertEqual(
            rgw.calls,
            [
                ("structureInfo", "NFFADI"),
                ("bucketDelete", "NFFADI", "owner-cnr-iom-ts-existing"),
            ],
        )
        self.assertFalse(Bucket.objects.filter(id=bucket.id).exists())

    def test_share_updates_rgwsquared_before_persisting_permission(self):
        bucket = self.local_bucket()
        rgw = RecordingRGWClient(users=["owner", "target"])
        rgw.share_probe = (bucket, self.target)
        request = self.authenticated_request(
            "post",
            f"/api/buckets/{bucket.id}/shares/",
            self.owner,
            {"username": "target", "permission": "ro"},
        )

        with patch("storage.views.buckets._get_rgw_client", return_value=rgw):
            response = self.view_shares(request, pk=bucket.id)

        self.assertEqual(response.status_code, 201, response.data)
        self.assertFalse(rgw.share_exists_at_update)
        self.assertIn(
            (
                "bucketUpdate",
                "NFFADI",
                "owner-cnr-iom-ts-existing",
                ["owner"],
                ["target"],
            ),
            rgw.calls,
        )
        self.assertTrue(
            BucketPermission.objects.filter(
                bucket=bucket, user=self.target, permission="ro", source="local"
            ).exists()
        )


class SyncServiceTests(TestCase):
    def test_refresh_uses_bucket_list_and_user_info_without_credentials(self):
        tenant, _ = Tenant.objects.update_or_create(
            code="NFFADI",
            defaults={
                "name": "NFFA-DI",
                "rgwsquared_structure": "NFFADI",
            },
        )

        class SyncClient:
            def get_structure_info(self, structure):
                return {"initialized": True}

            def list_buckets(self, structure):
                return [
                    {
                        "name": "NFFADI:275",
                        "auto": True,
                        "manual": False,
                        "RWPermissions": [],
                        "ROPermissions": [],
                    },
                    {
                        "name": "manual-bucket",
                        "auto": False,
                        "manual": True,
                        "RWPermissions": [],
                        "ROPermissions": [],
                    },
                ]

            def list_users(self, structure):
                return ["alice"]

            def get_user_info(self, structure, user):
                return {
                    "uid": "NFFADI$ext-users:alice",
                    "access_key": "USER_AK",
                    "secret_key": "USER_SK",
                    "RWBuckets": ["NFFADI:275"],
                    "ROBuckets": [],
                }

        stats = refresh_local_cache(tenant, client=SyncClient())

        self.assertTrue(stats["initialized"])
        self.assertEqual(stats["buckets_synced"], 2)
        proposal = Bucket.objects.get(name="275", tenant=tenant)
        manual = Bucket.objects.get(name="manual-bucket", tenant=tenant)
        self.assertEqual(proposal.bucket_type, Bucket.PROPOSAL)
        self.assertEqual(manual.bucket_type, Bucket.LOCAL)

        membership = TenantMembership.objects.get(tenant=tenant, ceph_username="alice")
        self.assertFalse(hasattr(membership, "s3_access_key"))
        self.assertFalse(hasattr(tenant, "mgmt_access_key"))
        self.assertTrue(
            BucketPermission.objects.filter(
                bucket=proposal,
                user=membership.user,
                permission="rw",
                source="rgwsquared",
            ).exists()
        )

    def test_refresh_clears_stale_uo_from_read_only_membership(self):
        tenant, _ = Tenant.objects.update_or_create(
            code="NFFADI",
            defaults={"name": "NFFA-DI", "rgwsquared_structure": "NFFADI"},
        )
        UOMapping.objects.create(
            tenant=tenant,
            uo_code="cnr-iom.ts",
            institution_name="CNR - Istituto Officina dei Materiali - Trieste",
        )
        user = User.objects.create_user(
            username="reader",
            email="reader@example.com",
            external_id="sub-reader",
            institution="CNR - Istituto Officina dei Materiali - Trieste",
        )
        TenantMembership.objects.create(
            user=user,
            tenant=tenant,
            ceph_username="reader",
            role="ro",
            uo_code="stale-code",
        )

        class SyncClient:
            def get_structure_info(self, structure):
                return {"initialized": True}

            def list_buckets(self, structure):
                return [{"name": "NFFADI:proposal", "auto": True, "manual": False}]

            def list_users(self, structure):
                return ["reader"]

            def get_user_info(self, structure, username):
                return {"ROBuckets": ["NFFADI:proposal"], "RWBuckets": []}

        stats = refresh_local_cache(tenant, client=SyncClient())

        membership = TenantMembership.objects.get(user=user, tenant=tenant)
        self.assertEqual(membership.role, "ro")
        self.assertEqual(membership.uo_code, "")
        self.assertEqual(stats["uo_codes_cleared"], 1)

    def test_fetch_mgmt_keys_requires_initialized_structure(self):
        tenant, _ = Tenant.objects.update_or_create(
            code="NFFADI",
            defaults={
                "name": "NFFA-DI",
                "rgwsquared_structure": "NFFADI",
            },
        )

        class UninitializedClient:
            def get_structure_info(self, structure):
                return {"initialized": False}

        with self.assertRaisesRegex(RuntimeError, "not initialized"):
            fetch_mgmt_keys(tenant, client=UninitializedClient())
