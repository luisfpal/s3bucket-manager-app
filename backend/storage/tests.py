import time
from unittest.mock import patch

import requests
from django.test import TestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from storage.models import Bucket, BucketPermission, Tenant, TenantMembership, User
from storage.services.rgw_squared import RGWSquaredClient, RGWSquaredError
from storage.services.s3_ops import fetch_mgmt_keys
from storage.services.sync_service import refresh_local_cache
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
