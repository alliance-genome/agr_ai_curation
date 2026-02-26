"""Unit tests for user_service provisioning and claim mapping."""

from types import SimpleNamespace

import pytest

from src.services import user_service


class _DummyQuery:
    def __init__(self, existing_user):
        self._existing_user = existing_user

    def filter_by(self, **_kwargs):
        return self

    def one_or_none(self):
        return self._existing_user


class _DummyDB:
    def __init__(self, existing_user=None):
        self.existing_user = existing_user
        self.added = []
        self.commit_calls = 0
        self.refresh_calls = 0

    def query(self, _model):
        return _DummyQuery(self.existing_user)

    def add(self, obj):
        self.added.append(obj)
        self.existing_user = obj

    def commit(self):
        self.commit_calls += 1

    def refresh(self, obj):
        self.refresh_calls += 1
        if getattr(obj, "id", None) is None:
            obj.id = 1


def _principal(subject="sub-1", email="user@example.org", display_name="User"):
    return SimpleNamespace(subject=subject, email=email, display_name=display_name)


def test_principal_from_claims_prefers_groups_list():
    claims = {"sub": "u1", "email": "u1@example.org", "name": "U1", "groups": ["a", "b"]}
    principal = user_service.principal_from_claims(claims, provider="cognito")
    assert principal.subject == "u1"
    assert principal.groups == ["a", "b"]
    assert principal.provider == "cognito"


def test_principal_from_claims_uses_cognito_groups_and_scalar_fallback():
    claims = {"sub": "u2", "email": "u2@example.org", "cognito:groups": "WB_curators"}
    principal = user_service.principal_from_claims(claims, provider="cognito")
    assert principal.groups == ["WB_curators"]


def test_principal_from_claims_defaults_missing_subject_to_empty():
    principal = user_service.principal_from_claims({"email": "u3@example.org"})
    assert principal.subject == ""
    assert principal.display_name == "u3@example.org"


def test_provision_weaviate_tenants_success(monkeypatch):
    created = []

    class _Collection:
        def __init__(self, name):
            self.name = name
            self.tenants = self

        def create(self, tenant):
            created.append((self.name, tenant.name))

    class _Client:
        class collections:
            @staticmethod
            def get(name):
                return _Collection(name)

    class _Conn:
        def session(self):
            class _Ctx:
                def __enter__(self_inner):
                    return _Client()

                def __exit__(self_inner, exc_type, exc, tb):
                    return False

            return _Ctx()

    monkeypatch.setattr(user_service, "get_connection", lambda: _Conn())
    monkeypatch.setattr(user_service, "get_tenant_name", lambda sub: f"tenant_{sub}")

    assert user_service.provision_weaviate_tenants("abc") is True
    assert ("DocumentChunk", "tenant_abc") in created
    assert ("PDFDocument", "tenant_abc") in created


def test_provision_weaviate_tenants_failure(monkeypatch):
    monkeypatch.setattr(user_service, "get_connection", lambda: (_ for _ in ()).throw(RuntimeError("down")))
    assert user_service.provision_weaviate_tenants("abc") is False


def test_provision_user_creates_new_user(monkeypatch):
    class _FakeUser:
        def __init__(self, **kwargs):
            self.id = None
            for key, value in kwargs.items():
                setattr(self, key, value)

    db = _DummyDB(existing_user=None)
    monkeypatch.setattr(user_service, "User", _FakeUser)
    tenant_calls = []
    monkeypatch.setattr(user_service, "provision_weaviate_tenants", lambda sub: tenant_calls.append(sub) or True)

    result = user_service.provision_user(db, _principal(subject="sub-new", email="new@example.org", display_name="New"))

    assert result.auth_sub == "sub-new"
    assert result.email == "new@example.org"
    assert db.commit_calls == 1
    assert db.refresh_calls == 1
    assert tenant_calls == ["sub-new"]


def test_provision_user_updates_existing_user(monkeypatch):
    existing = SimpleNamespace(
        id=42,
        auth_sub="sub-existing",
        email="old@example.org",
        display_name="Old Name",
        last_login=None,
    )
    db = _DummyDB(existing_user=existing)
    tenant_calls = []
    monkeypatch.setattr(user_service, "provision_weaviate_tenants", lambda sub: tenant_calls.append(sub) or True)

    result = user_service.provision_user(
        db,
        _principal(subject="sub-existing", email="new@example.org", display_name="New Name"),
    )

    assert result.id == 42
    assert result.email == "new@example.org"
    assert result.display_name == "New Name"
    assert result.last_login is not None
    assert db.commit_calls == 1
    assert tenant_calls == ["sub-existing"]


def test_provision_user_rejects_missing_subject():
    with pytest.raises(ValueError):
        user_service.provision_user(_DummyDB(), _principal(subject="", email="x@example.org", display_name="X"))
